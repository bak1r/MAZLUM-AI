"""
Runtime capability registry.
Gerçek yetenek haritası — prompt değil, runtime kontrolü.
Her tool/özellik için: durum (ACTIVE/PARTIAL/SCAFFOLD/MISSING) + Mac uyumluluğu.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(str, Enum):
    ACTIVE = "ACTIVE"          # Çalışıyor, test edildi
    PARTIAL = "PARTIAL"        # Kısmen çalışıyor, eksikler var
    SCAFFOLD = "SCAFFOLD"      # Dosya var ama gerçek iş yapmıyor
    MISSING = "MISSING"        # Hiç yok


class MacCompat(str, Enum):
    TESTED = "MAC_TESTED"
    UNTESTED = "MAC_UNTESTED"
    INCOMPATIBLE = "MAC_INCOMPATIBLE"
    NA = "NA"  # platform-agnostic (API çağrısı vb.)


@dataclass
class Capability:
    name: str
    description: str
    status: Status
    mac_compat: MacCompat
    details: str = ""


# Statik capability map — runtime'da güncellenir
_CAPABILITIES: dict[str, Capability] = {
    "db_query": Capability(
        name="db_query",
        description="Veritabanı sorgusu (read-only SQL)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="PostgreSQL bağlantısı, read-only enforced, schema context inject",
    ),
    "web_search": Capability(
        name="web_search",
        description="Web araması (DuckDuckGo)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="DuckDuckGo üzerinden web araması",
    ),
    "telegram_bot": Capability(
        name="telegram_bot",
        description="Telegram Bot API entegrasyonu",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="python-telegram-bot, polling mode, brain.process() bağlı",
    ),
    "web_ui": Capability(
        name="web_ui",
        description="Web arayüzü (FastAPI + WebSocket)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="FastAPI + WebSocket chat, minimal HTML UI",
    ),
    "intent_router": Capability(
        name="intent_router",
        description="Keyword-based domain/intent sınıflandırma",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="LLM çağrısı yapmadan domain/intent/complexity tespit",
    ),
    "memory": Capability(
        name="memory",
        description="Bellek sistemi (CRUD)",
        status=Status.PARTIAL,
        mac_compat=MacCompat.NA,
        details="Manuel CRUD çalışıyor, otomatik yazma (write gate) henüz aktif değil",
    ),
    "voice": Capability(
        name="voice",
        description="Ses girişi/çıkışı (STT/TTS)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.TESTED,
        details="Gemini Live API, PyAudio, Orus sesi, VAD, barge-in, echo cancellation, reconnect. Canlı mikrofon testi gerekli.",
    ),
    "browser_control": Capability(
        name="browser_control",
        description="URL açma (basit tarayıcı kontrolü)",
        status=Status.PARTIAL,
        mac_compat=MacCompat.TESTED,
        details="open_url ile URL açabilir, CDP ile karmaşık browser otomasyonu henüz yok.",
    ),
    "app_launcher": Capability(
        name="app_launcher",
        description="Uygulama açma/kapama",
        status=Status.ACTIVE,
        mac_compat=MacCompat.TESTED,
        details="Mac: open -a ile 30+ uygulama, osascript ile kapatma. Test edildi.",
    ),
    "file_controller": Capability(
        name="file_controller",
        description="Dosya işlemleri (liste, aç, oluştur)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.TESTED,
        details="list_files, open_file, create_file — Mac'te çalışıyor.",
    ),
    "desktop_control": Capability(
        name="desktop_control",
        description="Bilgisayar ayarları (ses, ekran görüntüsü)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.TESTED,
        details="osascript ile ses kontrol, screencapture ile ekran görüntüsü.",
    ),
    "memory_learning": Capability(
        name="memory_learning",
        description="Konusmadan ogrenme (remember_fact tool)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="LLM konusmadan onemli is bilgisini tespit edip hafizaya kaydedebilir. "
                "remember_fact tool'u ile 9 kategoride bilgi biriktirir.",
    ),
    "callback_alerting": Capability(
        name="callback_alerting",
        description="Dead letter callback uyarı sistemi",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="5dk periyodik kontrol, Telegram'a site bazlı dağılımla uyarı, spam koruması.",
    ),
    "proactive_monitoring": Capability(
        name="proactive_monitoring",
        description="Proaktif DB izleme (dead letter, bekleyen işlem, hızlı artış)",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="3 kural, sesli+Telegram bildirim, cooldown, config'den threshold.",
    ),
    "document_creation": Capability(
        name="document_creation",
        description="Word (.docx) ve Excel (.xlsx) belge oluşturma",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="python-docx + openpyxl, Markdown parse, auto-width, styled headers.",
    ),
    "screen_vision": Capability(
        name="screen_vision",
        description="Ekran görüntüsü alıp Vision AI ile analiz",
        status=Status.ACTIVE,
        mac_compat=MacCompat.TESTED,
        details="macOS screencapture + Gemini 2.5 Flash Vision.",
    ),
    "telegram_monitor": Capability(
        name="telegram_monitor",
        description="Telegram User API ile gerçek zamanlı izleme",
        status=Status.ACTIVE,
        mac_compat=MacCompat.NA,
        details="Telethon, mention/reply tespit, sesli bildirim, arama, mesaj okuma/gönderme.",
    ),
}


class CapabilityRegistry:
    """Runtime capability registry."""

    def __init__(self):
        self._caps = dict(_CAPABILITIES)

    def get_active(self) -> list[Capability]:
        """Sadece ACTIVE durumundaki yetenekleri döndür."""
        return [c for c in self._caps.values() if c.status == Status.ACTIVE]

    def get_all(self) -> list[Capability]:
        """Tüm yetenekleri döndür."""
        return list(self._caps.values())

    def get_status(self, name: str) -> Optional[Capability]:
        """Belirli bir yeteneğin durumunu döndür."""
        return self._caps.get(name)

    def update_status(self, name: str, status: Status, details: str = ""):
        """Yetenek durumunu güncelle."""
        if name in self._caps:
            self._caps[name].status = status
            if details:
                self._caps[name].details = details

    def build_capability_prompt(self) -> str:
        """System prompt'a enjekte edilecek yetenek bilgisi üret."""
        active = []
        unavailable = []

        for cap in self._caps.values():
            if cap.status == Status.ACTIVE:
                active.append(f"- {cap.description}")
            elif cap.status == Status.PARTIAL:
                active.append(f"- {cap.description} (kısıtlı)")
            elif cap.status in (Status.SCAFFOLD, Status.MISSING):
                unavailable.append(f"- {cap.description}")

        parts = ["[Gerçek Yetenekler]"]
        if active:
            parts.append("Yapabildiğin:\n" + "\n".join(active))
        if unavailable:
            parts.append("Henüz yapamadığın:\n" + "\n".join(unavailable))
        parts.append("Yapamadığın bir şey sorulursa dürüstçe söyle.")

        return "\n".join(parts)
