"""
Voice I/O Engine — Gemini Live API ile gerçek zamanlı ses.

Mimari:
- Gemini Live = ana ses döngüsü (STT + TTS tek session'da)
- MAZLUM brain = Gemini'nin tool'u olarak çağrılır (karmaşık işler için)
- PyAudio = mikrofon girişi + hoparlör çıkışı
- VAD = Voice Activity Detection (echo cancellation)
- Barge-in = kullanıcı konuşurken kesebilir
"""
import asyncio
import logging
import os
import struct
import re
import threading
import time
from typing import Optional, Callable

log = logging.getLogger("seriai.io.voice")

# Audio constants
FORMAT_INT16 = 8  # pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
BARGE_IN_RMS = 4000  # Kullanıcı sesi eşiği — hoparlör echo'su (~300-1000) geçilir, insan sesi (~2000+) algılanır
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"


class VoiceEngine:
    """
    Gemini Live tabanlı gerçek zamanlı ses motoru.
    MAZLUM voice interface.
    """

    def __init__(self, config, brain=None, telegram_monitor=None):
        self.config = config
        self.brain = brain  # MAZLUM brain — tool olarak çağrılacak
        self.telegram_monitor = telegram_monitor  # Telegram User API monitörü
        self._running = False
        self._session = None
        self._loop = None
        self._is_speaking = False
        self._audio_in_queue = None
        self._out_queue = None
        self._pya = None
        self._on_text_received: Optional[Callable] = None
        self._on_log: Optional[Callable] = None
        self._broadcast_fn: Optional[Callable] = None  # web UI broadcast
        self._notification_queue: asyncio.Queue = None  # proaktif bildirimler
        self._notification_pending = False  # Bildirim enjekte edildi, ghost filtre devre dışı
        self._recent_notifications: list = []  # Son bildirimler — Gemini hafızası için
        self.mic_muted = False  # Web UI mute butonu

    def set_broadcast(self, broadcast_fn: Callable):
        """Web UI'a event broadcast fonksiyonunu bağla."""
        self._broadcast_fn = broadcast_fn

    async def inject_notification(self, text: str):
        """Dışarıdan sesli bildirim enjekte et (Telegram mention, alert, vb.)."""
        if self._notification_queue:
            await self._notification_queue.put(text)

    async def _notification_listener(self):
        """Kuyruktan gelen bildirimleri Gemini'ye enjekte et."""
        while self._running:
            try:
                text = await asyncio.wait_for(
                    self._notification_queue.get(), timeout=5
                )
                if self._session and not self._is_speaking:
                    self._log(f"Proaktif bildirim: {text[:80]}")
                    self._notification_pending = True

                    # Hafızada tut — "ne dedin?" diye sorarsa hatırlasın
                    from datetime import datetime
                    self._recent_notifications.append({
                        "time": datetime.now().strftime("%H:%M"),
                        "text": text,
                    })
                    # Son 10 bildirimi tut
                    if len(self._recent_notifications) > 10:
                        self._recent_notifications = self._recent_notifications[-10:]

                    # Gemini'ye güçlü talimatla gönder
                    inject_text = (
                        f"[SİSTEM BİLDİRİMİ — KULLANICIYA HEMEN SESLİ BİLDİR]\n"
                        f"{text}\n"
                        f"Bu bildirimi kullanıcıya kısa ve net şekilde sesli olarak ilet. "
                        f"'Efendim, az önce Telegram'da sizi etiketlediler...' gibi başla."
                    )
                    await self._session.send_client_content(
                        turns={"parts": [{"text": inject_text}]},
                        turn_complete=True,
                    )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"Bildirim hatası: {e}")

    async def _broadcast(self, event_type: str, data: dict):
        """Web UI'a event gönder (varsa)."""
        if self._broadcast_fn:
            try:
                await self._broadcast_fn(event_type, data)
            except Exception as e:
                log.warning(f"Broadcast hatası ({event_type}): {e}")

    def set_text_handler(self, handler: Callable):
        self._on_text_received = handler

    def set_log_handler(self, handler: Callable):
        self._on_log = handler

    def _log(self, msg: str):
        log.info(msg)
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:
                pass

    def is_available(self) -> bool:
        """Check if voice engine can run on this platform."""
        try:
            import pyaudio
            return True
        except ImportError:
            return False

    def _get_api_key(self) -> str:
        key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if key:
            return key
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if key:
            return key
        key = os.environ.get("GOOGLE_GENAI_API_KEY", "").strip()
        return key

    def _build_tool_declarations(self) -> list:
        """Gemini tool'ları — sadece fiziksel aksiyonlar + brain delegasyonu.

        MİMARİ: Gemini = kulak + ağız (STT/TTS)
                 Claude Brain = beyin (tüm düşünme, analiz, karar)
                 Telegram tools = direkt Telethon API (hızlı, brain gerektirmez)
        """
        tools = [
            # ══════════════════════════════════════════════
            # ANA BEYİN — her türlü düşünme/analiz/karar buraya
            # ══════════════════════════════════════════════
            {
                "name": "seriai_brain",
                "description": (
                    "MAZLUM beyni — HER TÜRLÜ düşünme gerektiren iş için çağır. "
                    "Veritabanı sorgusu, iş analizi, rapor, Word/Excel belgesi oluşturma, "
                    "web araması, bilgi sorgulama, hesaplama, karşılaştırma, strateji. "
                    "Kullanıcı bir şey SORDUĞUNDA veya YAPILMASINI İSTEDİĞİNDE çağır. "
                    "Sadece 'merhaba', 'teşekkürler' gibi basit sohbet için çağırma."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "user_request": {
                            "type": "STRING",
                            "description": "Kullanıcının isteği, kelimesi kelimesine ve TAM olarak."
                        }
                    },
                    "required": ["user_request"]
                }
            },
            # ══════════════════════════════════════════════
            # FİZİKSEL AKSİYONLAR — düşünme gerektirmez
            # ══════════════════════════════════════════════
            {
                "name": "open_app",
                "description": "Uygulama aç: Chrome, Telegram, Word, Excel, Spotify, vb.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "app_name": {"type": "STRING", "description": "Uygulama adı"}
                    },
                    "required": ["app_name"]
                }
            },
            {
                "name": "open_url",
                "description": "URL'yi tarayıcıda aç. YouTube, Google, web siteleri için BUNU kullan, open_app DEĞİL.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {"type": "STRING", "description": "Açılacak URL (https://...)"}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "computer_settings",
                "description": "Bilgisayar ses ayarları ve ekran görüntüsü. SADECE kullanıcı açıkça ses/volume değiştirmek istediğinde çağır. Rastgele sayılar veya belirsiz komutlar için ÇAĞIRMA.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {"type": "STRING", "description": "volume_up|volume_down|mute|unmute|get_volume|screenshot"},
                        "value": {"type": "STRING", "description": "Opsiyonel değer"}
                    },
                    "required": ["action"]
                }
            },
            {
                "name": "self_shutdown",
                "description": "MAZLUM'u kapat.",
                "parameters": {"type": "OBJECT", "properties": {}, "required": []}
            },
            # ══════════════════════════════════════════════
            # TELEGRAM — direkt API, hızlı, brain gerektirmez
            # ══════════════════════════════════════════════
            {
                "name": "check_telegram_mentions",
                "description": "Telegram'da beni etiketleyen mesajları getir.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "limit": {"type": "STRING", "description": "Kaç tane (varsayılan: 10)"}
                    },
                    "required": []
                }
            },
            {
                "name": "telegram_summary",
                "description": "Telegram okunmamış mesaj/mention özeti.",
                "parameters": {"type": "OBJECT", "properties": {}, "required": []}
            },
            {
                "name": "search_telegram",
                "description": "Telegram'da mesaj ara.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {"type": "STRING", "description": "Aranacak kelime"},
                        "chat_name": {"type": "STRING", "description": "Sadece bu chatte ara (opsiyonel)"},
                        "limit": {"type": "STRING", "description": "Kaç sonuç (varsayılan: 10)"}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "read_telegram_chat",
                "description": "Bir sohbetin son mesajlarını OKU (sadece sana metin döner, ekranda açmaz).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chat_name": {"type": "STRING", "description": "Sohbet adı"},
                        "chat_id": {"type": "STRING", "description": "Sohbet ID (opsiyonel)"},
                        "limit": {"type": "STRING", "description": "Kaç mesaj (varsayılan: 10)"}
                    },
                    "required": []
                }
            },
            {
                "name": "open_telegram_chat",
                "description": "Telegram uygulamasında bir sohbeti EKRANDA AÇ.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chat_name": {"type": "STRING", "description": "Sohbet/grup adı"},
                        "chat_id": {"type": "STRING", "description": "Sohbet ID (opsiyonel)"}
                    },
                    "required": []
                }
            },
            {
                "name": "reply_telegram",
                "description": "Telegram'da mesaj gönder. Önce kullanıcıya ne göndereceğini onayla.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chat_name": {"type": "STRING", "description": "Sohbet adı"},
                        "chat_id": {"type": "STRING", "description": "Sohbet ID (opsiyonel)"},
                        "text": {"type": "STRING", "description": "Mesaj metni"}
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "mark_telegram_read",
                "description": "Mesajları okundu işaretle. Boş bırakırsan TÜM chatler.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "chat_name": {"type": "STRING", "description": "Sohbet adı (boş = hepsi)"},
                        "chat_id": {"type": "STRING", "description": "Sohbet ID (opsiyonel)"}
                    },
                    "required": []
                }
            },
            # ══════════════════════════════════════════════
            # FİZİKSEL — Ekran izleme
            # ══════════════════════════════════════════════
            {
                "name": "screen_check",
                "description": "Ekranın görüntüsünü al ve analiz et. 'Ekranda ne var?', 'ne görüyorsun?' sorularında kullan.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "question": {"type": "STRING", "description": "Ekran hakkında spesifik soru (opsiyonel)"}
                    },
                    "required": []
                }
            },
            {
                "name": "list_telegram_chats",
                "description": "Telegram sohbet listesini getir. Grup, kanal, kişi filtrelenebilir.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filter": {"type": "STRING", "description": "Filtre: group, channel, user (opsiyonel)"},
                        "limit": {"type": "STRING", "description": "Kaç sohbet (varsayılan: 20)"}
                    },
                    "required": []
                }
            },
        ]
        return tools

    def _build_config(self):
        """Gemini Live session config."""
        from google.genai import types

        system_prompt = (
            "Sen MAZLUM — serialhavale.com'un sesli asistanı, sahibinin sağ kolu.\n\n"

            "KİŞİLİĞİN:\n"
            "- Samimi, zeki, cesur. Gerçek bir arkadaş gibi konuş — kalıp cümle tekrarlama.\n"
            "- Kısa ve öz ama soğuk değil. Espri duruma göre: kuru mizah, ironi. Klişe YAPMA.\n"
            "- Kötü haberi de samimi ver. Cesur ol — 'Bu mantıksız' diyebilirsin.\n"
            "- Küfüre bozulma, tonunu koru. Uydurma bilgi üretme — bilmiyorsan tool çağır.\n"
            "- Türkçe konuş.\n\n"

            "KİMLİK:\n"
            "- 'Serial' = serialhavale.com ödeme platformu. DİZİ DEĞİL.\n"
            "- Bahis altyapısı ödeme sistemi. Ekipler, işlemler, çekimler hep bu platformla ilgili.\n\n"

            "TOOL KULLANIMI:\n"
            "- seriai_brain → soru, analiz, belge, DB/işlem/callback. Emin değilsen de çağır.\n"
            "  30-90sn sürebilir. 'Bakıyorum bi saniye' gibi doğal geçiştir.\n"
            "- Selamlaşma/teşekkür/veda → direkt cevap, tool çağırma.\n"
            "- Uygulama → open_app. Ses → computer_settings. Ekran → screen_check.\n"
            "- Telegram: oku → read_telegram_chat, aç → open_telegram_chat, yaz → reply_telegram (önce onay al!).\n"
            "- Sonucu insanca özetle, kuru rapor okuma. Tool çağırmadan 'yaptım' DEME.\n\n"

            "BİLDİRİMLER:\n"
            "- [SİSTEM BİLDİRİMİ] → hemen söyle. 'Ne dedin?' → son bildirimi tekrarla.\n"
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=system_prompt,
            tools=[{"function_declarations": self._build_tool_declarations()}],
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    silence_duration_ms=800,
                ),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Orus"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc):
        """Tool çağrısı — Gemini bir tool çağırdığında burası çalışır."""
        from google.genai import types
        name = fc.name
        args = dict(fc.args or {})
        self._log(f"Tool: {name} args={args}")
        await self._broadcast("tool_state", {"name": name, "state": "running"})
        await self._broadcast("thinking", {"active": True, "text": f"Tool: {name}"})

        loop = asyncio.get_running_loop()
        result = "Tamamlandı."

        try:
            if name == "seriai_brain":
                # MAZLUM beynini çağır (timeout: 150s — MAX_TOOL_ROUNDS=4 × 30s DB + LLM)
                if self.brain:
                    try:
                        resp = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: self.brain.process(args.get("user_request", ""), context={"source": "voice"})
                            ),
                            timeout=300.0,
                        )
                        result = resp.text if (resp and resp.text and resp.text.strip()) else "Analiz tamamlandı ancak sonuç üretilemedi. Lütfen soruyu tekrar sorun."
                    except asyncio.TimeoutError:
                        result = (
                            "İstek zaman aşımına uğradı. "
                            "TEKRAR DENEME — aynı isteği tekrar çağırma, kullanıcıya durumu bildir."
                        )
                else:
                    result = "Brain bağlı değil."

            elif name == "open_app":
                app = args.get("app_name", "")
                from seriai.tools.desktop import open_app as _desktop_open_app
                result_dict = await loop.run_in_executor(None, lambda: _desktop_open_app(app_name=app))
                result = result_dict.get("result", result_dict.get("error", str(result_dict)))

            elif name == "open_url":
                url = args.get("url", "")
                if url:
                    from seriai.tools.desktop import open_url as _desktop_open_url
                    result_dict = await loop.run_in_executor(None, lambda: _desktop_open_url(url=url))
                    result = result_dict.get("result", result_dict.get("error", str(result_dict)))
                else:
                    result = "URL belirtilmedi."

            elif name == "computer_settings":
                action = args.get("action", "")
                value = args.get("value", "")
                from seriai.tools.desktop import computer_settings as _desktop_settings
                result_dict = await loop.run_in_executor(
                    None, lambda: _desktop_settings(action=action, value=value)
                )
                result = result_dict.get("result", result_dict.get("error", str(result_dict)))

            elif name == "screen_check":
                question = args.get("question", "")
                try:
                    from seriai.tools.vision import analyze_screen
                    result_dict = await loop.run_in_executor(
                        None, lambda: analyze_screen(question)
                    )
                    result = result_dict.get("result", result_dict.get("error", "Ekran analiz edilemedi."))
                except ImportError:
                    result = "Ekran analiz modülü yüklü değil."

            elif name == "self_shutdown":
                self._log("MAZLUM kapatılıyor...")
                result = "MAZLUM kapatılıyor, efendim. Görüşürüz."
                # Web UI'a kapanış bildirimi gönder
                async def _send_shutdown():
                    try:
                        await self._broadcast("shutdown", {"message": "MAZLUM kapatıldı. Görüşürüz."})
                    except Exception:
                        pass
                asyncio.ensure_future(_send_shutdown())
                import signal
                def _force_exit():
                    """SIGTERM → 2s bekle → SIGKILL. Web server dahil her şey kapanır."""
                    try:
                        from seriai.monitoring.telemetry import report_shutdown
                        report_shutdown("sesli komut ile kapatıldı")
                    except Exception:
                        pass
                    os.kill(os.getpid(), signal.SIGTERM)
                    import time; time.sleep(2)
                    os._exit(0)  # Hâlâ kapanmadıysa zorla kapat
                threading.Timer(2.5, _force_exit).start()

            elif name == "check_telegram_mentions":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "Telegram monitör aktif değil."
                else:
                    limit = int(args.get("limit", "10") or "10")
                    mentions = await asyncio.wait_for(
                        self.telegram_monitor.get_recent_mentions(limit=limit),
                        timeout=15.0,
                    )
                    if not mentions:
                        result = "Hiç mention/etiket bulunamadı."
                    else:
                        lines = [f"{len(mentions)} mention bulundu:"]
                        for m in mentions:
                            sender = m.get('sender', m.get('sender_name', '?'))
                            chat = m.get('chat_name', '?')
                            text = m.get('text', '')[:120]
                            date = m.get('date', m.get('timestamp', ''))
                            lines.append(f"- {sender} ({chat}) [{date}]: {text}")
                        result = "\n".join(lines)

            elif name == "telegram_summary":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "Telegram monitör aktif değil."
                else:
                    summary = await asyncio.wait_for(
                        self.telegram_monitor.get_summary(),
                        timeout=15.0,
                    )
                    if not summary.get("connected"):
                        result = "Telegram bağlı değil."
                    else:
                        lines = [
                            f"Telegram: {summary.get('user', '?')}",
                            f"Toplam okunmamış: {summary.get('total_unread', 0)} mesaj",
                            f"Okunmamış mention: {summary.get('total_unread_mentions', 0)}",
                        ]
                        top = summary.get("top_chats", [])
                        if top:
                            lines.append("En çok okunmamış:")
                            for c in top[:5]:
                                mention_tag = f" ({c['mentions']} mention)" if c.get('mentions') else ""
                                lines.append(f"  - {c['name']}: {c['unread']} mesaj{mention_tag}")
                        result = "\n".join(lines)

            elif name == "search_telegram":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "Telegram monitör aktif değil."
                else:
                    query = args.get("query", "")
                    chat_name = args.get("chat_name", "")
                    limit = int(args.get("limit", "10") or "10")
                    if not query:
                        result = "Aranacak kelime gerekli."
                    else:
                        # Chat ismiyle arama
                        chat_id = None
                        if chat_name:
                            found = await self.telegram_monitor.find_chat(chat_name)
                            if found:
                                chat_id = found["id"]

                        msgs = await asyncio.wait_for(
                            self.telegram_monitor.search_messages(
                                query=query, limit=limit, chat_id=chat_id
                            ),
                            timeout=15.0,
                        )
                        if not msgs:
                            result = f"'{query}' ile ilgili mesaj bulunamadı."
                        else:
                            lines = [f"'{query}' araması — {len(msgs)} sonuç:"]
                            for m in msgs:
                                direction = "Sen" if m.get("is_outgoing") else m.get("sender", "?")
                                chat = m.get("chat_name", "?")
                                date = m.get("date", "")
                                text = m.get("text", "")[:120]
                                lines.append(f"- [{direction}] ({chat}) [{date}]: {text}")
                            result = "\n".join(lines)

            elif name == "list_telegram_chats":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "Telegram monitör aktif değil."
                else:
                    filter_type = args.get("filter", "") or None
                    limit = int(args.get("limit", "20") or "20")
                    chats = await self.telegram_monitor.list_dialogs(
                        limit=limit, filter_type=filter_type
                    )
                    if not chats:
                        result = "Sohbet bulunamadı."
                    else:
                        lines = [f"{len(chats)} sohbet:"]
                        for c in chats:
                            unread = f" [{c['unread_count']} okunmamış]" if c.get("unread_count") else ""
                            mention = f" ({c['unread_mentions']} mention)" if c.get("unread_mentions") else ""
                            lines.append(f"- {c['name']} ({c['type']}) ID:{c['id']}{unread}{mention}")
                        result = "\n".join(lines)

            elif name == "read_telegram_chat":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "Telegram monitör aktif değil."
                else:
                    chat_name = args.get("chat_name", "")
                    chat_id_str = args.get("chat_id", "0") or "0"
                    chat_id = int(chat_id_str) if chat_id_str != "0" else None
                    limit = int(args.get("limit", "10") or "10")

                    messages = await asyncio.wait_for(
                        self.telegram_monitor.get_chat_messages(
                            chat_id=chat_id, chat_name=chat_name, limit=limit
                        ),
                        timeout=15.0,
                    )
                    if not messages:
                        result = "Mesaj bulunamadı veya sohbet erişilemez."
                    else:
                        lines = [f"Son {len(messages)} mesaj:"]
                        for m in messages:
                            direction = "Sen" if m["is_outgoing"] else m["sender"]
                            lines.append(f"- [{direction}] [{m.get('date','')}]: {m['text'][:150]}")
                        result = "\n".join(lines)

            elif name == "open_telegram_chat":
                # Telegram'da belirli bir chat'i EKRANDA aç
                chat_name = args.get("chat_name", "")
                chat_id_str = args.get("chat_id", "0") or "0"
                chat_id = int(chat_id_str) if chat_id_str != "0" else None
                _tg_opened = False

                # Telethon'dan chat bilgisi al
                if self.telegram_monitor and self.telegram_monitor.is_connected:
                    if not chat_id and chat_name:
                        found = await asyncio.wait_for(
                            self.telegram_monitor.find_chat(chat_name),
                            timeout=15.0,
                        )
                        if found:
                            chat_id = found["id"]
                            chat_name = found.get("name", chat_name)

                    if chat_id:
                        import subprocess

                        # Entity bilgisi al
                        username = None
                        entity_type = None
                        try:
                            entity = await self.telegram_monitor._client.get_entity(chat_id)
                            username = getattr(entity, 'username', None)
                            entity_type = type(entity).__name__  # Chat, Channel, User
                        except Exception:
                            pass

                        # Telegram'ı öne getir
                        subprocess.Popen(["open", "-a", "Telegram"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        await asyncio.sleep(0.5)

                        # Yöntem 1: Username varsa deep link (en güvenilir)
                        if username:
                            link = f"tg://resolve?domain={username}"
                            subprocess.Popen(["open", link],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            _tg_opened = True
                            result = f"Telegram'da '{chat_name}' sohbeti açıldı."
                            self._log(f"Telegram chat açıldı: {chat_name} (link: {link})")

                        # Yöntem 2: Channel/Supergroup — t.me/c/ linki
                        elif entity_type == "Channel":
                            peer_id = abs(chat_id)
                            # -100 prefix'i kaldır (Telethon marked ID)
                            if str(peer_id).startswith("100") and len(str(peer_id)) > 10:
                                channel_id = str(peer_id)[3:]
                            else:
                                channel_id = str(peer_id)
                            link = f"https://t.me/c/{channel_id}/999999999"
                            subprocess.Popen(["open", link],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            _tg_opened = True
                            result = f"Telegram'da '{chat_name}' sohbeti açıldı."
                            self._log(f"Telegram chat açıldı: {chat_name} (link: {link})")

                        # Yöntem 3: Basic group — AppleScript ile Cmd+K arama
                        else:
                            safe_name = chat_name.replace('"', '').replace("'", "").replace("\\", "")
                            script = (
                                'tell application "Telegram" to activate\n'
                                'delay 0.3\n'
                                'tell application "System Events"\n'
                                '    keystroke "k" using command down\n'
                                '    delay 0.3\n'
                                f'    keystroke "{safe_name}"\n'
                                '    delay 1.0\n'
                                '    key code 36\n'
                                'end tell'
                            )
                            try:
                                proc = subprocess.run(
                                    ["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=8
                                )
                                if proc.returncode == 0:
                                    _tg_opened = True
                                    result = f"Telegram'da '{chat_name}' sohbeti açıldı."
                                    self._log(f"Telegram chat açıldı (AppleScript): {chat_name}")
                                else:
                                    # AppleScript başarısız — Accessibility izni yok
                                    # Son çare: deep link dene
                                    peer_id = abs(chat_id)
                                    link = f"tg://openmessage?chat_id={peer_id}"
                                    subprocess.Popen(["open", link],
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                    _tg_opened = True
                                    result = (
                                        f"'{chat_name}' için deep link açıldı. "
                                        "Not: Basic group'ları ekranda açmak için "
                                        "System Preferences → Privacy → Accessibility'den "
                                        "Terminal/Python'a izin verin."
                                    )
                                    self._log(f"Telegram chat fallback: {chat_name} (link: {link})")
                            except subprocess.TimeoutExpired:
                                _tg_opened = False
                            except Exception as e:
                                self._log(f"Telegram chat açma hatası: {e}")
                                _tg_opened = False

                if not _tg_opened:
                    if not chat_name:
                        result = "Chat adı veya ID gerekli."
                    else:
                        result = f"'{chat_name}' bulunamadı veya Telegram monitör aktif değil."

            elif name == "reply_telegram":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "Telegram monitör aktif değil."
                else:
                    chat_name = args.get("chat_name", "")
                    chat_id_str = args.get("chat_id", "0") or "0"
                    chat_id = int(chat_id_str) if chat_id_str != "0" else None
                    text = args.get("text", "")
                    if not text:
                        result = "Mesaj metni gerekli."
                    else:
                        raw = await asyncio.wait_for(
                            self.telegram_monitor.send_reply(
                                chat_id=chat_id, chat_name=chat_name, text=text
                            ),
                            timeout=15.0,
                        )
                        result = raw if isinstance(raw, str) else str(raw)

            elif name == "mark_telegram_read":
                if not self.telegram_monitor or not self.telegram_monitor.is_connected:
                    result = "BAŞARISIZ: Telegram monitör aktif değil."
                else:
                    chat_name = args.get("chat_name", "")
                    chat_id_str = args.get("chat_id", "0") or "0"
                    chat_id = int(chat_id_str) if chat_id_str != "0" else None
                    result = await asyncio.wait_for(
                        self.telegram_monitor.mark_as_read(
                            chat_id=chat_id, chat_name=chat_name
                        ),
                        timeout=15.0,
                    )

            else:
                result = f"BAŞARISIZ: Bilinmeyen araç: {name}"

        except asyncio.TimeoutError:
            result = f"BAŞARISIZ: {name} zaman aşımına uğradı (15s). Telegram yavaş yanıt veriyor."
            log.warning(f"Tool {name} timeout (15s)")
        except Exception as e:
            result = f"BAŞARISIZ: Araç hatası ({name}): {e}"
            log.error(f"Tool {name} failed: {e}")
            from seriai.monitoring.telemetry import report
            report("voice.tool", e, context=f"tool={name}")

        # Kodsal prefix: Gemini'ye net başarı/hata sinyali
        # Sadece zaten BAŞARISIZ prefix'i olan veya çok kısa hata mesajları kontrol et
        # Uzun brain cevaplarında "hata" kelimesi metin içinde geçebilir — false positive!
        if result.startswith("BAŞARISIZ"):
            pass  # Zaten işaretli
        elif len(result) < 150:
            # Kısa sonuçlarda hata kelimesi gerçekten hata demektir
            _fail_words = ("hata", "error", "bulunamadı", "aktif değil",
                           "açılamadı", "gönderilemedi", "zaman aşımı", "erişilemez")
            if any(w in result.lower() for w in _fail_words):
                result = f"BAŞARISIZ: {result}"
            elif not result.startswith("BAŞARILI"):
                result = f"BAŞARILI: {result}"
        elif not result.startswith("BAŞARILI"):
            # Uzun sonuç = büyük ihtimalle başarılı brain response
            result = f"BAŞARILI: {result}"

        self._log(f"Tool sonuç: {result[:100]}")
        await self._broadcast("tool_state", {"name": name, "state": "done"})
        await self._broadcast("thinking", {"active": False})
        return types.FunctionResponse(
            id=fc.id,
            name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        """Mikrofon verilerini Gemini'ye gönder."""
        _MAX_FRAME_SIZE = CHUNK_SIZE * 2 + 64
        while self._running:
            msg = await self._out_queue.get()
            frame_data = msg.get("data", b"")
            if len(frame_data) > _MAX_FRAME_SIZE:
                msg = {"data": frame_data[:CHUNK_SIZE * 2], "mime_type": "audio/pcm"}
            # Queue depth guard
            if self._out_queue.qsize() > 10:
                drained = 0
                while not self._out_queue.empty() and drained < 5:
                    try:
                        self._out_queue.get_nowait()
                        drained += 1
                    except asyncio.QueueEmpty:
                        break
            session = self._session
            if session:
                try:
                    await session.send_realtime_input(media=msg)
                except Exception as e:
                    raise ConnectionError(f"Session lost: {e}")
            else:
                raise ConnectionError("Session is None")

    async def _listen_audio(self):
        """Mikrofon dinle — VAD + echo cancellation + UI broadcast."""
        import pyaudio
        self._log("Mikrofon başlatıldı")
        stream = await asyncio.to_thread(
            self._pya.open,
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        SILENCE = b"\x00" * (CHUNK_SIZE * 2)
        _was_speaking = False
        _transition_cooldown = 0.0
        _COOLDOWN_MS = 100  # Echo transition cooldown
        _post_reconnect_end = getattr(self, '_post_reconnect_cooldown', 0.0)
        _post_speak_mute_end = 0.0  # Extra mute after MAZLUM speaks
        _POST_SPEAK_MUTE_MS = 1000  # 1s mute after speaking — laptop hoparlör echo sönümlemesi

        # Audio level broadcast throttle
        _last_level_broadcast = 0.0
        _LEVEL_INTERVAL = 0.1  # 100ms between level broadcasts

        try:
            while self._running:
                data = await asyncio.to_thread(
                    stream.read, CHUNK_SIZE, exception_on_overflow=False
                )
                now_mono = time.monotonic()

                if now_mono < _post_reconnect_end:
                    await self._out_queue.put({"data": SILENCE, "mime_type": "audio/pcm"})
                    continue

                currently_speaking = self._is_speaking
                if _was_speaking and not currently_speaking:
                    _transition_cooldown = now_mono + (_COOLDOWN_MS / 1000.0)
                    _post_speak_mute_end = now_mono + (_POST_SPEAK_MUTE_MS / 1000.0)
                    while not self._out_queue.empty():
                        try:
                            self._out_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                elif not _was_speaking and currently_speaking:
                    _transition_cooldown = now_mono + (_COOLDOWN_MS / 1000.0)
                _was_speaking = currently_speaking

                if now_mono < _transition_cooldown:
                    await self._out_queue.put({"data": SILENCE, "mime_type": "audio/pcm"})
                    continue

                # RMS hesapla
                rms = 0.0
                try:
                    samples = struct.unpack(f'<{CHUNK_SIZE}h', data)
                    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                except Exception:
                    pass

                # Broadcast audio level to web UI (throttled)
                if now_mono - _last_level_broadcast > _LEVEL_INTERVAL:
                    _last_level_broadcast = now_mono
                    level = 0.0 if self.mic_muted else min(1.0, rms / 3000.0)
                    await self._broadcast("audio_level", {"level": level})

                # Mic muted → sadece sessizlik gönder
                if self.mic_muted:
                    await self._out_queue.put({"data": SILENCE, "mime_type": "audio/pcm"})
                elif self._is_speaking or now_mono < _post_speak_mute_end:
                    # Bot konuşurken echo'yu engelle AMA barge-in için yüksek sesi geçir
                    # Kullanıcı "dur" dediğinde Gemini algılayabilsin
                    if rms > BARGE_IN_RMS:
                        # Yüksek ses = kullanıcı konuşuyor → barge-in sinyali
                        await self._out_queue.put({"data": data, "mime_type": "audio/pcm"})
                    else:
                        await self._out_queue.put({"data": SILENCE, "mime_type": "audio/pcm"})
                else:
                    await self._out_queue.put({"data": data, "mime_type": "audio/pcm"})
        except Exception as e:
            log.error(f"Mikrofon hatası: {e}")
            raise
        finally:
            stream.close()

    async def _receive_audio(self):
        """Gemini'den gelen ses + transcript + tool call'ları işle."""
        self._log("Alıcı başlatıldı")
        out_buf = []
        in_buf = []

        try:
            while self._running:
                session = self._session
                if not session:
                    raise ConnectionError("Session is None")
                turn = session.receive()
                async for response in turn:
                    if response.data:
                        if not self._is_speaking:
                            self._is_speaking = True
                            await self._broadcast("speaking", {"active": True})
                        self._audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        # Barge-in
                        if getattr(sc, "interrupted", False):
                            self._is_speaking = False
                            while not self._audio_in_queue.empty():
                                try:
                                    self._audio_in_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                            self._log("Kullanıcı kesti (barge-in)")
                            await self._broadcast("speaking", {"active": False})
                            out_buf = []

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                # Cancel/dur komutu algıla — SADECE kısa, net iptal komutları
                                lower = txt.replace("İ", "i").replace("I", "ı").lower().strip().rstrip(".!?,")
                                # Tam eşleşme veya çok kısa cümleler (max 3 kelime)
                                cancel_exact = ["dur", "sus", "kes", "iptal", "vazgeç", "stop", "durdur", "yeter", "tamam dur"]
                                word_count = len(lower.split())
                                is_cancel = lower in cancel_exact or (word_count <= 3 and any(lower == cw or lower.startswith(cw + " ") for cw in cancel_exact))
                                if is_cancel:
                                    self._log(f"İptal komutu algılandı: '{txt}'")
                                    # Audio kuyruğunu temizle
                                    while not self._audio_in_queue.empty():
                                        try:
                                            self._audio_in_queue.get_nowait()
                                        except asyncio.QueueEmpty:
                                            break
                                    self._is_speaking = False
                                    await self._broadcast("speaking", {"active": False})
                                    break

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.turn_complete:
                            self._is_speaking = False
                            full_in = " ".join(in_buf).strip() if in_buf else ""
                            full_out = " ".join(out_buf).strip() if out_buf else ""
                            # Gemini bazen kontrol tokenları üretir (<ctrl46> vb.) — temizle
                            if full_out:
                                full_out = re.sub(r'<ctrl\d+>', '', full_out).strip()
                            in_buf = []
                            out_buf = []

                            # Ghost response filtresi — input yokken gelen kısa yanıtlar
                            # Proaktif bildirim yanıtını FİLTRELEME
                            if not full_in and full_out and not self._notification_pending:
                                ghost_words = ["evet", "hayır", "tamam", "anladım", "peki",
                                               "hm", "hmm", "pardon"]
                                if any(full_out.replace("İ","i").replace("I","ı").lower().strip().startswith(g) for g in ghost_words) and len(full_out) < 20:
                                    self._log(f"Ghost yanıt filtrelendi: {full_out}")
                                    await self._broadcast("speaking", {"active": False})
                                    await self._broadcast("thinking", {"active": False})
                                    continue
                            self._notification_pending = False

                            if full_in:
                                self._log(f"Sen: {full_in}")
                                await self._broadcast("transcript", {"role": "user", "text": full_in})
                            if full_out:
                                self._log(f"MAZLUM: {full_out}")
                                await self._broadcast("transcript", {
                                    "role": "ai", "text": full_out,
                                    "model": "gemini-voice", "domain": "voice",
                                })
                            await self._broadcast("speaking", {"active": False})
                            await self._broadcast("thinking", {"active": False})

                            # Text handler callback
                            if full_in and self._on_text_received:
                                try:
                                    self._on_text_received(full_in)
                                except Exception:
                                    pass

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        session = self._session
                        if session:
                            try:
                                await session.send_tool_response(
                                    function_responses=fn_responses
                                )
                            except Exception:
                                self._log("Tool response gönderilemedi — session kapanmış")

        except Exception as e:
            self._is_speaking = False
            while not self._audio_in_queue.empty():
                try:
                    self._audio_in_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            log.error(f"Alıcı hatası: {e}")
            raise

    async def _play_audio(self):
        """Hoparlörden Gemini sesini çal + audio level broadcast."""
        import pyaudio
        self._log("Hoparlör başlatıldı")
        stream = await asyncio.to_thread(
            self._pya.open,
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        _last_level = 0.0
        try:
            while self._running:
                try:
                    chunk = await asyncio.wait_for(self._audio_in_queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue  # Re-check self._running
                await asyncio.to_thread(stream.write, chunk)
                # Broadcast speaking audio level for brain animation
                now_mono = time.monotonic()
                if now_mono - _last_level > 0.08:  # ~12fps
                    _last_level = now_mono
                    try:
                        n = len(chunk) // 2
                        if n > 0:
                            samples = struct.unpack(f'<{n}h', chunk)
                            rms = (sum(s * s for s in samples) / n) ** 0.5
                            level = min(1.0, rms / 5000.0)
                            await self._broadcast("audio_level", {"level": level})
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"Hoparlör hatası: {e}")
            raise
        finally:
            stream.close()

    async def _keepalive_heartbeat(self):
        """WebSocket timeout'u önlemek için 60s'de bir silence gönder + UI heartbeat."""
        _heartbeat_count = 0
        while self._running:
            try:
                await asyncio.sleep(10)
                _heartbeat_count += 1

                # UI heartbeat every 10s — keep daemon vital alive
                await self._broadcast("daemon_status", {"voice": "ok"})

                # Gemini keepalive every 60s
                if _heartbeat_count % 6 == 0 and self._session:
                    silence = b"\x00" * 320
                    await self._session.send_realtime_input(
                        media={"data": silence, "mime_type": "audio/pcm"}
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"Heartbeat hatası: {e}")
                continue

    async def run_async(self):
        """Ana ses döngüsü — bağlan, dinle, konuş, reconnect."""
        import pyaudio
        from google import genai

        api_key = self._get_api_key()
        if not api_key:
            log.error("Google API key bulunamadı. Voice başlatılamıyor.")
            return

        self._pya = pyaudio.PyAudio()
        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"}
        )
        self._running = True
        _reconnect_count = 0

        while self._running:
            try:
                self._log("Bağlanıyor...")
                config = self._build_config()

                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                    self._session = session
                    self._loop = asyncio.get_running_loop()
                    self._audio_in_queue = asyncio.Queue()
                    self._out_queue = asyncio.Queue(maxsize=10)
                    self._notification_queue = asyncio.Queue()

                    is_reconnect = _reconnect_count > 0
                    _reconnect_count += 1
                    self._log(f"Bağlandı. (session #{_reconnect_count})")
                    await self._broadcast("daemon_status", {"voice": "ok"})

                    if is_reconnect:
                        self._post_reconnect_cooldown = time.monotonic() + 2.0

                    # Karşılama
                    if not is_reconnect:
                        await session.send_client_content(
                            turns={"parts": [{"text": (
                                "[SISTEM] Kullanıcı MAZLUM'u başlattı. "
                                "Kısa ve sıcak bir karşılama yap. "
                                "'Merhaba efendim, nasıl yardımcı olabilirim?' tarzı bir giriş."
                            )}]},
                            turn_complete=True,
                        )
                    else:
                        await session.send_client_content(
                            turns={"parts": [{"text": (
                                "[SISTEM] Bağlantı koptu ve yeniden kuruldu. "
                                "Kendini tekrar tanıtma. "
                                "Kısa 'tekrar buradayım' de."
                            )}]},
                            turn_complete=True,
                        )

                    tasks = [
                        asyncio.create_task(self._send_realtime()),
                        asyncio.create_task(self._listen_audio()),
                        asyncio.create_task(self._receive_audio()),
                        asyncio.create_task(self._play_audio()),
                        asyncio.create_task(self._keepalive_heartbeat()),
                        asyncio.create_task(self._notification_listener()),
                    ]
                    try:
                        await asyncio.gather(*tasks)
                    except Exception:
                        for t in tasks:
                            t.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        raise

            except asyncio.CancelledError:
                self._running = False
                self._session = None
                raise  # Propagate so run_voice() handler runs

            except Exception as e:
                err_str = str(e)
                self._is_speaking = False
                # Exponential backoff: 2s, 4s, 8s, 16s, 32s, max 60s
                _reconnect_delay = min(2 * (2 ** min(_reconnect_count - 1, 5)), 60)
                if "1008" in err_str:
                    log.warning("WebSocket 1008 policy error — session rebuild")
                    _reconnect_delay = 2  # policy error: fast retry
                elif "1011" in err_str:
                    log.warning("WebSocket 1011 keepalive timeout — reconnecting")
                    _reconnect_delay = 1  # keepalive: fast retry
                elif "quota" in err_str.lower() or "rate" in err_str.lower() or "429" in err_str:
                    log.error(f"Voice API quota/rate limit: {e}")
                    from seriai.monitoring.telemetry import report
                    report("voice.quota", e, context="API quota/rate limit", severity="CRITICAL")
                    _reconnect_delay = min(_reconnect_delay * 2, 120)  # quota: extra slow
                else:
                    log.error(f"Voice hatası: {e}")
            finally:
                self._session = None

            if self._running:
                self._log(f"Yeniden bağlanıyor ({_reconnect_delay}s)...")
                await asyncio.sleep(_reconnect_delay)

        # Cleanup
        if self._pya:
            try:
                self._pya.terminate()
            except Exception:
                pass

    def stop(self):
        """Voice engine'i durdur."""
        self._running = False
        self._session = None  # Break any session-dependent loops
        # PyAudio terminate run_voice()'daki cleanup'ta yapılıyor — double terminate engelle
        log.info("Voice engine durduruldu.")
