"""
Telegram User API Monitor — Telethon ile kullanıcı hesabını gerçek zamanlı izler.

Yetenekler:
- Tüm gelen mesajları dinler (real-time)
- Mention/tag tespit eder (@username, first_name, reply)
- Geçmiş mesajlarda arama yapar (search across all chats)
- Okunmamış mesajları kontrol eder
- Chat listesi döndürür (isimle arama)
- Belirli bir chat'in mesajlarını okur
- Kullanıcı adına yanıt gönderir
- Web UI'a broadcast eder
- Sesli bildirim sistemi ile entegre

Gerekli env:
- SERIAI_TG_API_ID: Telegram API ID (my.telegram.org)
- SERIAI_TG_API_HASH: Telegram API hash
- SERIAI_TG_PHONE: Kullanıcının telefon numarası (+905xx...)
"""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

log = logging.getLogger("seriai.monitoring.telegram_monitor")

# Session dosyası
SESSION_PATH = Path(__file__).resolve().parents[2] / "data" / "telegram_user"

# Limits
MAX_MENTION_HISTORY = 200
MAX_MESSAGE_HISTORY = 500


class TelegramMonitor:
    """
    Telethon tabanlı Telegram kullanıcı hesabı monitörü.
    Gerçek zamanlı izleme + geçmiş arama + tam kontrol.
    """

    def __init__(self):
        self._client = None
        self._running = False
        self._connected = False
        self._broadcast_fn: Optional[Callable] = None
        self._on_mention_fn: Optional[Callable] = None  # async callback for mention/reply
        self._me = None  # Kullanıcı bilgisi
        self._recent_mentions: List[Dict] = []
        self._recent_messages: List[Dict] = []
        self._dialog_cache: Dict[int, Dict] = {}  # chat_id -> {name, type, ...}
        self._lock = asyncio.Lock()
        self._reconnecting = False

        # Env'den config
        self._api_id = os.getenv("SERIAI_TG_API_ID", "").strip()
        self._api_hash = os.getenv("SERIAI_TG_API_HASH", "").strip()
        self._phone = os.getenv("SERIAI_TG_PHONE", "").strip()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    @property
    def my_info(self) -> Optional[Dict]:
        if not self._me:
            return None
        return {
            "id": self._me.id,
            "first_name": self._me.first_name or "",
            "last_name": getattr(self._me, "last_name", "") or "",
            "username": self._me.username or "",
            "phone": self._me.phone or "",
        }

    def set_broadcast(self, broadcast_fn: Callable):
        self._broadcast_fn = broadcast_fn

    def set_on_mention(self, callback: Callable):
        """Set async callback for mention/reply notifications.
        Callback signature: async def on_mention(sender_name, chat_name, text_preview, reason)
        """
        self._on_mention_fn = callback

    async def _broadcast(self, event_type: str, data: dict):
        if self._broadcast_fn:
            try:
                await self._broadcast_fn(event_type, data)
            except Exception as e:
                log.warning(f"Broadcast hatası ({event_type}): {e}")

    def _is_configured(self) -> bool:
        return bool(self._api_id and self._api_hash and self._phone)

    # ═══════════════════════════════════════════════════════════════
    # BAŞLATMA / BAĞLANTI
    # ═══════════════════════════════════════════════════════════════

    async def start(self):
        """Telegram User API monitörünü başlat."""
        if not self._is_configured():
            log.warning(
                "Telegram User API yapılandırılmamış. "
                "SERIAI_TG_API_ID, SERIAI_TG_API_HASH, SERIAI_TG_PHONE ayarlayın."
            )
            return

        try:
            from telethon import TelegramClient, events

            SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)

            self._client = TelegramClient(
                str(SESSION_PATH),
                int(self._api_id),
                self._api_hash,
                system_version="MAZLUM Monitor",
                device_model="MAZLUM",
                app_version="1.0",
            )

            log.info("Telegram User API'ye bağlanılıyor...")
            await self._client.start(phone=self._phone)

            self._me = await self._client.get_me()
            self._connected = True
            self._running = True

            log.info(
                f"Telegram bağlandı: {self._me.first_name} "
                f"(@{self._me.username or 'yok'}) — ID: {self._me.id}"
            )

            # Dialog cache'ini doldur
            await self._refresh_dialog_cache()

            await self._broadcast("note", {
                "text": f"Telegram monitör aktif: @{self._me.username or self._me.first_name}",
                "priority": "info",
            })

            # Mesaj handler — sadece ilk kez kayıt et (reconnect'te tekrar ekleme)
            if not getattr(self, '_handler_registered', False):
                @self._client.on(events.NewMessage(incoming=True))
                async def _on_new_message(event):
                    await self._handle_message(event)
                self._handler_registered = True

                # Watchdog + refresh — sadece ilk kez
                asyncio.create_task(self._connection_watchdog())
                asyncio.create_task(self._periodic_dialog_refresh())

            log.info("Telegram mesaj dinleyicisi aktif.")

        except ImportError:
            log.error("telethon yüklü değil. pip install telethon")
        except Exception as e:
            log.error(f"Telegram bağlantı hatası: {e}")
            self._connected = False
            if not self._reconnecting:
                asyncio.create_task(self._auto_reconnect())

    # ═══════════════════════════════════════════════════════════════
    # MESAJ İŞLEME (REAL-TIME)
    # ═══════════════════════════════════════════════════════════════

    async def _handle_message(self, event):
        """Gelen mesajı işle — mention tespiti ve broadcast."""
        try:
            msg = event.message
            if not msg:
                return

            sender = await event.get_sender()
            chat = await event.get_chat()

            sender_name = self._get_entity_name(sender)
            sender_username = getattr(sender, "username", "") or "" if sender else ""
            chat_name = self._get_chat_name(chat, event.chat_id)
            chat_id = event.chat_id

            # Cache güncelle
            if chat_id not in self._dialog_cache and chat:
                self._dialog_cache[chat_id] = {
                    "id": chat_id,
                    "name": chat_name,
                    "type": self._get_chat_type(chat),
                }

            is_mention = self._detect_mention(msg)
            is_reply_to_me = await self._is_reply_to_me(msg)

            msg_data = {
                "chat_id": chat_id,
                "chat_name": chat_name,
                "sender_name": sender_name,
                "sender_username": sender_username,
                "text": (msg.text or "[medya]")[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message_id": msg.id,
                "is_mention": is_mention or is_reply_to_me,
                "is_reply_to_me": is_reply_to_me,
            }

            # Mesaj geçmişine ekle
            async with self._lock:
                self._recent_messages.insert(0, msg_data)
                if len(self._recent_messages) > MAX_MESSAGE_HISTORY:
                    self._recent_messages = self._recent_messages[:MAX_MESSAGE_HISTORY]

            if is_mention or is_reply_to_me:
                async with self._lock:
                    self._recent_mentions.insert(0, msg_data)
                    if len(self._recent_mentions) > MAX_MENTION_HISTORY:
                        self._recent_mentions = self._recent_mentions[:MAX_MENTION_HISTORY]

                reason = "etiketledi" if is_mention else "mesajınıza yanıt verdi"
                log.info(f"TG mention: {sender_name} ({chat_name}): {(msg.text or '')[:80]}")

                await self._broadcast("telegram_mention", {
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "sender": sender_name,
                    "sender_username": sender_username,
                    "text": (msg.text or "")[:300],
                    "timestamp": msg_data["timestamp"],
                    "is_reply": is_reply_to_me,
                })

                # Mention callback — voice notification etc.
                if self._on_mention_fn:
                    try:
                        await self._on_mention_fn(
                            sender_name=sender_name,
                            chat_name=chat_name,
                            text_preview=(msg.text or "")[:100],
                            reason=reason,
                        )
                    except Exception as cb_err:
                        log.error(f"Mention callback hatası: {cb_err}")

            await self._broadcast("telegram_message", msg_data)

        except Exception as e:
            log.error(f"Mesaj işleme hatası: {e}")

    def _detect_mention(self, msg) -> bool:
        """Entity-based + text-based mention tespiti."""
        if not self._me:
            return False
        try:
            from telethon.tl.types import MessageEntityMention, MessageEntityMentionName

            text = (msg.text or "").lower()
            my_username = (self._me.username or "").lower()
            my_first_name = (self._me.first_name or "").lower()
            my_id = self._me.id

            # Entity tabanlı
            if msg.entities:
                for entity in msg.entities:
                    if isinstance(entity, MessageEntityMention):
                        mention_text = msg.text[entity.offset:entity.offset + entity.length].lower()
                        if my_username and mention_text.lstrip("@") == my_username:
                            return True
                    elif isinstance(entity, MessageEntityMentionName):
                        if entity.user_id == my_id:
                            return True

            # Text-based @username
            if my_username and f"@{my_username}" in text:
                return True

            # NOT: İsim tabanlı mention KALDIRILDI — "doğan" gibi yaygın isimler
            # müşteri adlarında da geçiyor ve false positive üretiyor.
            # Sadece @username, entity mention ve reply_to_me güvenilir.

            return False
        except Exception:
            return False

    async def _is_reply_to_me(self, msg) -> bool:
        """Mesaj bana reply mi?"""
        if not self._me or not msg.reply_to:
            return False
        try:
            reply_msg = await msg.get_reply_message()
            if reply_msg and reply_msg.sender_id == self._me.id:
                return True
        except Exception:
            pass
        return False

    # ═══════════════════════════════════════════════════════════════
    # GEÇMİŞ ARAMA (TARİHSEL)
    # ═══════════════════════════════════════════════════════════════

    async def search_messages(self, query: str, limit: int = 20, chat_id: int = None) -> List[Dict]:
        """Tüm chatlerde veya belirli bir chatte mesaj ara.
        Global arama Telethon'un SearchGlobalRequest'ini kullanır (hızlı)."""
        if not self.is_connected:
            return []

        results = []

        try:
            # Telethon: entity=None → global arama (SearchGlobalRequest)
            # entity=chat_id → belirli chatte arama
            entity = chat_id if chat_id else None

            async for msg in self._client.iter_messages(
                entity, limit=limit, search=query
            ):
                sender_name = "Bilinmeyen"
                try:
                    sender = await msg.get_sender()
                    sender_name = self._get_entity_name(sender)
                except Exception:
                    pass

                chat_name = str(msg.chat_id or "?")
                cached = self._dialog_cache.get(msg.chat_id)
                if cached:
                    chat_name = cached["name"]
                else:
                    try:
                        chat = await msg.get_chat()
                        chat_name = self._get_chat_name(chat, msg.chat_id)
                    except Exception:
                        pass

                results.append({
                    "chat_id": msg.chat_id,
                    "chat_name": chat_name,
                    "sender": sender_name,
                    "text": (msg.text or "[medya]")[:300],
                    "date": msg.date.strftime("%d.%m %H:%M") if msg.date else "",
                    "message_id": msg.id,
                    "is_outgoing": msg.out,
                })

            return results

        except Exception as e:
            log.error(f"Mesaj arama hatası: {e}")
            return []

    async def get_unread_mentions(self, limit: int = 20) -> List[Dict]:
        """Okunmamış mention'ları Telethon ile çek — sadece unread_mentions > 0 olan dialog'lar."""
        if not self.is_connected:
            return []

        try:
            from telethon.tl.types import InputMessagesFilterMyMentions

            results = []
            # SADECE unread mention'ı olan dialog'ları tara (hızlı)
            async for dialog in self._client.iter_dialogs(limit=None):
                if dialog.unread_mentions_count <= 0:
                    continue
                if len(results) >= limit:
                    break
                try:
                    async for msg in self._client.iter_messages(
                        dialog.entity,
                        limit=min(dialog.unread_mentions_count, 5),
                        filter=InputMessagesFilterMyMentions(),
                    ):
                        sender_name = "Bilinmeyen"
                        try:
                            sender = await msg.get_sender()
                            sender_name = self._get_entity_name(sender)
                        except Exception:
                            pass

                        results.append({
                            "chat_id": dialog.id,
                            "chat_name": dialog.name or str(dialog.id),
                            "sender": sender_name,
                            "text": (msg.text or "[medya]")[:300],
                            "date": msg.date.strftime("%d.%m %H:%M") if msg.date else "",
                            "message_id": msg.id,
                        })
                        if len(results) >= limit:
                            break
                except Exception:
                    continue

            return results
        except Exception as e:
            log.error(f"Okunmamış mention hatası: {e}")
            return []

    async def get_recent_mentions(self, limit: int = 10) -> List[Dict]:
        """Real-time yakalanan + geçmiş mention'ları birleştir.
        Eğer yeterli sonuç yoksa @username ile global arama yapar."""
        if not self.is_connected:
            return []

        # 1. Real-time yakalananlar
        async with self._lock:
            rt_mentions = list(self._recent_mentions[:limit])

        # 2. Okunmamış mention'lar
        if len(rt_mentions) < limit:
            remaining = limit - len(rt_mentions)
            historical = await self.get_unread_mentions(limit=remaining)
            existing_ids = {(m.get("chat_id"), m.get("message_id")) for m in rt_mentions}
            for h in historical:
                key = (h.get("chat_id"), h.get("message_id"))
                if key not in existing_ids:
                    rt_mentions.append(h)
                    existing_ids.add(key)

        # 3. Hâlâ az varsa → @username ile global arama (geçmiş mention'lar)
        if len(rt_mentions) < limit and self._me:
            remaining = limit - len(rt_mentions)
            existing_ids = {(m.get("chat_id"), m.get("message_id")) for m in rt_mentions}

            # @username ile ara (kesin mention — false positive riski düşük)
            username = self._me.username
            if username:
                search_results = await self.search_messages(
                    query=f"@{username}", limit=remaining
                )
                for r in search_results:
                    key = (r.get("chat_id"), r.get("message_id"))
                    if key not in existing_ids and not r.get("is_outgoing"):
                        r["is_mention"] = True
                        rt_mentions.append(r)
                        existing_ids.add(key)
            # NOT: İsim araması yapılmıyor — false positive çok yüksek
            # "Doğan" gibi yaygın isimler yüzlerce alakasız sonuç döndürür

        return rt_mentions[:limit]

    # ═══════════════════════════════════════════════════════════════
    # CHAT LİSTESİ / ARAMA
    # ═══════════════════════════════════════════════════════════════

    async def list_dialogs(self, limit: int = 200, filter_type: str = None) -> List[Dict]:
        """Kullanıcının chat/grup listesini döndür. Önce cache, sonra API."""
        if not self.is_connected:
            return []

        try:
            results = []
            # Her zaman API kullan — unread count doğru olsun
            async for dialog in self._client.iter_dialogs(limit=limit):
                dtype = "user"
                if dialog.is_group:
                    dtype = "group"
                elif dialog.is_channel:
                    dtype = "channel"

                if filter_type and dtype != filter_type:
                    continue

                results.append({
                    "id": dialog.id,
                    "name": dialog.name or str(dialog.id),
                    "type": dtype,
                    "unread_count": dialog.unread_count,
                    "unread_mentions": dialog.unread_mentions_count,
                    "last_message": (dialog.message.text or "[medya]")[:100] if dialog.message else "",
                    "last_date": dialog.date.strftime("%d.%m %H:%M") if dialog.date else "",
                })

            return results
        except Exception as e:
            log.error(f"Dialog listesi hatası: {e}")
            return []

    async def find_chat(self, name: str) -> Optional[Dict]:
        """İsimle chat ara — cache'de fuzzy matching, yoksa API."""
        if not self.is_connected:
            return None

        name_lower = name.lower().strip()

        # 1. Cache'de ara (hızlı — 300+ chat zaten yüklü)
        best_match = None
        best_score = 0
        for cid, info in self._dialog_cache.items():
            cname = info["name"].lower()
            if name_lower == cname:
                return {"id": cid, **info}  # Tam eşleşme
            if name_lower in cname:
                # Kısa isim daha iyi eşleşme (daha spesifik)
                score = len(name_lower) / max(len(cname), 1)
                if score > best_score:
                    best_score = score
                    best_match = {"id": cid, **info}

        if best_match:
            return best_match

        # 2. Cache'de yoksa API ile ara — scoring ile en iyi eşleşme
        try:
            api_best = None
            api_best_score = 0
            async for dialog in self._client.iter_dialogs(limit=None):
                dname = (dialog.name or "").lower()
                if name_lower == dname:
                    # Tam eşleşme — hemen döndür
                    result = {
                        "id": dialog.id,
                        "name": dialog.name or str(dialog.id),
                        "type": "group" if dialog.is_group else "channel" if dialog.is_channel else "user",
                    }
                    self._dialog_cache[dialog.id] = result
                    return result
                if name_lower in dname:
                    score = len(name_lower) / max(len(dname), 1)
                    if score > api_best_score:
                        api_best_score = score
                        api_best = {
                            "id": dialog.id,
                            "name": dialog.name or str(dialog.id),
                            "type": "group" if dialog.is_group else "channel" if dialog.is_channel else "user",
                        }
                        self._dialog_cache[dialog.id] = api_best
            if api_best:
                return api_best
        except Exception as e:
            log.error(f"Chat arama hatası: {e}")

        return None

    # ═══════════════════════════════════════════════════════════════
    # MESAJ OKUMA / GÖNDERME
    # ═══════════════════════════════════════════════════════════════

    async def get_chat_messages(self, chat_id: int = None, chat_name: str = None, limit: int = 15) -> List[Dict]:
        """Belirli bir chat'in mesajlarını oku. ID veya isimle."""
        if not self.is_connected:
            return []

        # İsimle arama
        if not chat_id and chat_name:
            found = await self.find_chat(chat_name)
            if found:
                chat_id = found["id"]
            else:
                return []

        if not chat_id:
            return []

        try:
            messages = []
            async for msg in self._client.iter_messages(chat_id, limit=limit):
                sender_name = "Bilinmeyen"
                try:
                    sender = await msg.get_sender()
                    sender_name = self._get_entity_name(sender)
                except Exception:
                    pass

                messages.append({
                    "id": msg.id,
                    "sender": sender_name,
                    "text": (msg.text or "[medya]")[:400],
                    "date": msg.date.strftime("%d.%m %H:%M") if msg.date else "",
                    "is_outgoing": msg.out,
                })

            return messages
        except Exception as e:
            log.error(f"Mesaj okuma hatası (chat={chat_id}): {e}")
            return []

    async def send_reply(self, chat_id: int = None, chat_name: str = None, text: str = "") -> str:
        """Mesaj gönder — ID veya isimle."""
        if not self.is_connected:
            return "Telegram monitör aktif değil."

        if not chat_id and chat_name:
            found = await self.find_chat(chat_name)
            if found:
                chat_id = found["id"]
            else:
                return f"'{chat_name}' adında bir sohbet bulunamadı."

        if not chat_id or not text:
            return "Sohbet ve mesaj gerekli."

        try:
            await self._client.send_message(chat_id, text)
            chat_info = self._dialog_cache.get(chat_id, {})
            cname = chat_info.get("name", str(chat_id))
            log.info(f"TG yanıt: {cname} → {text[:50]}")
            return f"Mesaj gönderildi: {cname}"
        except Exception as e:
            return f"Gönderilemedi: {e}"

    async def mark_as_read(self, chat_id: int = None, chat_name: str = None) -> str:
        """Mesajları okundu olarak işaretle. chat_id/chat_name verilmezse TÜM chatler."""
        if not self.is_connected:
            return "Telegram monitör aktif değil."

        try:
            if chat_id or chat_name:
                # Belirli bir chat
                if not chat_id and chat_name:
                    found = await self.find_chat(chat_name)
                    if found:
                        chat_id = found["id"]
                    else:
                        return f"'{chat_name}' bulunamadı."
                entity = await self._client.get_entity(chat_id)
                await self._client.send_read_acknowledge(entity)
                cname = self._dialog_cache.get(chat_id, {}).get("name", str(chat_id))
                return f"'{cname}' okundu olarak işaretlendi."
            else:
                # TÜM chatler — rate limit korumalı
                count = 0
                errors = 0
                async for dialog in self._client.iter_dialogs(limit=None):
                    if dialog.unread_count > 0:
                        try:
                            await self._client.send_read_acknowledge(dialog.entity)
                            count += 1
                            # FloodWait koruması: her 20 chat'te 1s bekle
                            if count % 20 == 0:
                                await asyncio.sleep(1.0)
                        except Exception as e:
                            errors += 1
                            if "FloodWait" in str(e) or "flood" in str(e).lower():
                                # Rate limit — dur ve devam etme
                                break
                suffix = f" ({errors} hata)" if errors else ""
                return f"{count} sohbet okundu olarak işaretlendi{suffix}."
        except Exception as e:
            log.error(f"Okundu işaretleme hatası: {e}")
            return f"Okundu işaretlenemedi: {e}"

    # ═══════════════════════════════════════════════════════════════
    # ÖZET / DURUM
    # ═══════════════════════════════════════════════════════════════

    async def get_summary(self) -> Dict:
        """Telegram durumu özeti — okunmamışlar, mention'lar, aktif chatler."""
        if not self.is_connected:
            return {"connected": False}

        try:
            total_unread = 0
            total_mentions = 0
            top_unread = []

            async for dialog in self._client.iter_dialogs(limit=None):
                if dialog.unread_count > 0:
                    total_unread += dialog.unread_count
                    total_mentions += dialog.unread_mentions_count
                    if dialog.unread_count >= 1:
                        top_unread.append({
                            "name": dialog.name or str(dialog.id),
                            "unread": dialog.unread_count,
                            "mentions": dialog.unread_mentions_count,
                            "last": (dialog.message.text or "")[:60] if dialog.message else "",
                        })

            top_unread.sort(key=lambda x: x["unread"], reverse=True)

            return {
                "connected": True,
                "user": f"@{self._me.username}" if self._me and self._me.username else self._me.first_name if self._me else "?",
                "total_unread": total_unread,
                "total_unread_mentions": total_mentions,
                "top_chats": top_unread[:10],
                "realtime_mentions": len(self._recent_mentions),
            }
        except Exception as e:
            log.error(f"Özet hatası: {e}")
            return {"connected": True, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # YARDIMCI FONKSIYONLAR
    # ═══════════════════════════════════════════════════════════════

    def _get_entity_name(self, entity) -> str:
        if not entity:
            return "Bilinmeyen"
        first = getattr(entity, "first_name", "") or ""
        last = getattr(entity, "last_name", "") or ""
        title = getattr(entity, "title", "") or ""
        if title:
            return title
        name = f"{first} {last}".strip()
        return name or "Bilinmeyen"

    def _get_chat_name(self, chat, chat_id: int = None) -> str:
        if chat:
            title = getattr(chat, "title", "") or ""
            if title:
                return title
            return self._get_entity_name(chat)
        cached = self._dialog_cache.get(chat_id)
        if cached:
            return cached["name"]
        return str(chat_id or "?")

    def _get_chat_type(self, chat) -> str:
        from telethon.tl.types import User, Chat, Channel
        if isinstance(chat, User):
            return "user"
        elif isinstance(chat, Chat):
            return "group"
        elif isinstance(chat, Channel):
            return "channel" if getattr(chat, "broadcast", False) else "group"
        return "unknown"

    async def _refresh_dialog_cache(self):
        """Dialog cache'ini yenile."""
        try:
            async for dialog in self._client.iter_dialogs(limit=None):
                dtype = "user"
                if dialog.is_group:
                    dtype = "group"
                elif dialog.is_channel:
                    dtype = "channel"
                self._dialog_cache[dialog.id] = {
                    "id": dialog.id,
                    "name": dialog.name or str(dialog.id),
                    "type": dtype,
                }
            log.info(f"Dialog cache: {len(self._dialog_cache)} sohbet yüklendi.")
        except Exception as e:
            log.warning(f"Dialog cache hatası: {e}")

    async def _periodic_dialog_refresh(self):
        """Her 5 dakikada dialog cache'ini yenile."""
        while self._running:
            try:
                await asyncio.sleep(300)
                if self.is_connected:
                    await self._refresh_dialog_cache()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _connection_watchdog(self):
        """Bağlantı durumunu kontrol et."""
        while self._running:
            try:
                await asyncio.sleep(30)
                if self._client and not self._client.is_connected():
                    log.warning("Telegram bağlantısı koptu, yeniden bağlanılıyor...")
                    self._connected = False
                    try:
                        await self._client.connect()
                        if await self._client.is_user_authorized():
                            self._connected = True
                            log.info("Telegram yeniden bağlandı.")
                    except Exception as e:
                        log.error(f"Telegram reconnect hatası: {e}")
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _auto_reconnect(self):
        """Başlangıç bağlantısı başarısız olunca yeniden dene."""
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            delays = [5, 10, 30, 60, 120]
            for attempt, delay in enumerate(delays, 1):
                log.info(f"Telegram reconnect denemesi {attempt}/{len(delays)} ({delay}s)...")
                await asyncio.sleep(delay)
                try:
                    await self.start()
                    if self._connected:
                        return
                except Exception as e:
                    log.error(f"Reconnect {attempt} başarısız: {e}")
            log.error("Telegram monitör: tüm denemeler başarısız.")
        finally:
            self._reconnecting = False

    async def stop(self):
        self._running = False
        self._connected = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        log.info("Telegram monitör durduruldu.")
