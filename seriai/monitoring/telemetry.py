"""
MAZLUM Telemetry — Remote error reporting.
Hata oluştuğunda sahibine Telegram üzerinden bildirim gönderir.

Mevcut sisteme SIFIR müdahale. Sadece hata yakalandığında report() çağrılır.
Gönderim başarısız olursa sessizce loglar, sistemi ASLA durdurmaz.
"""
import json
import logging
import os
import platform
import socket
import threading
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional, Union

log = logging.getLogger("seriai.telemetry")

# ── Config ─────────────────────────────────────────────────────────
_TELEMETRY_BOT_TOKEN = os.getenv("TELEMETRY_BOT_TOKEN", "")
_TELEMETRY_CHAT_ID = os.getenv("TELEMETRY_CHAT_ID", "")  # Sahibin chat ID'si
_OWNER_USER_ID = 5787979890  # @onlydogan — sadece bu kullanıcı erişebilir

# Rate limiting — aynı hata 5 dakika içinde tekrar gönderilmez
_RATE_LIMIT_SEC = 300
_recent_errors: dict[str, float] = {}
_lock = threading.Lock()

# ── Device Info (bir kez hesaplanır) ───────────────────────────────
_device_info: Optional[dict] = None


def _get_device_info() -> dict:
    """Cihaz bilgilerini topla (bir kez, cache'le)."""
    global _device_info
    if _device_info is not None:
        return _device_info

    # IP adresi (harici)
    external_ip = "bilinmiyor"
    try:
        req = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "MAZLUM-Telemetry/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            external_ip = data.get("ip", "bilinmiyor")
    except Exception:
        pass

    # Lokal IP
    local_ip = "bilinmiyor"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    _device_info = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "local_ip": local_ip,
        "external_ip": external_ip,
        "owner_name": os.getenv("OWNER_NAME", "bilinmiyor"),
    }
    return _device_info


def _send_telegram(text: str) -> bool:
    """Telegram mesajı gönder (raw HTTP, bağımlılık yok)."""
    if not _TELEMETRY_BOT_TOKEN or not _TELEMETRY_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{_TELEMETRY_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": _TELEMETRY_CHAT_ID,
        "text": text[:4000],  # Telegram limit
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log.debug(f"Telemetry send failed: {e}")
        return False


def _is_rate_limited(error_key: str) -> bool:
    """Aynı hata 5 dakika içinde tekrar gönderilmesin."""
    now = time.time()
    with _lock:
        # Eski kayıtları temizle
        expired = [k for k, t in _recent_errors.items() if now - t > _RATE_LIMIT_SEC]
        for k in expired:
            del _recent_errors[k]

        if error_key in _recent_errors:
            return True
        _recent_errors[error_key] = now
        return False


# ── Public API ─────────────────────────────────────────────────────

def report(
    source: str,
    error: Union[Exception, str],
    context: str = "",
    severity: str = "ERROR",
) -> None:
    """
    Hata raporla. Async-safe, thread-safe, fail-safe.
    Hiçbir durumda mevcut sistemi bozmaz.

    Args:
        source: Hatanın kaynağı (brain, voice, telegram, main)
        error: Exception veya hata mesajı
        context: Ek bağlam bilgisi
        severity: ERROR, WARNING, CRITICAL
    """
    try:
        # Token yoksa sessizce çık
        if not _TELEMETRY_BOT_TOKEN:
            return

        error_msg = str(error)
        tb = ""
        if isinstance(error, Exception):
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            # Son 500 karakter yeterli
            if len(tb) > 500:
                tb = "..." + tb[-500:]

        # Rate limit kontrolü
        error_key = f"{source}:{error_msg[:100]}"
        if _is_rate_limited(error_key):
            return

        # Cihaz bilgisi
        dev = _get_device_info()

        # Mesaj oluştur
        emoji = {"CRITICAL": "🔴", "ERROR": "🟠", "WARNING": "🟡"}.get(severity, "🟠")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        msg = (
            f"{emoji} <b>MAZLUM {severity}</b>\n"
            f"<b>Kaynak:</b> {source}\n"
            f"<b>Zaman:</b> {now}\n"
            f"<b>Cihaz:</b> {dev['hostname']}\n"
            f"<b>IP:</b> {dev['external_ip']} ({dev['local_ip']})\n"
            f"<b>Sahip:</b> {dev['owner_name']}\n"
            f"<b>Platform:</b> {dev['platform']}\n"
        )
        if context:
            msg += f"<b>Bağlam:</b> {context}\n"
        msg += f"\n<b>Hata:</b>\n<code>{error_msg[:500]}</code>"
        if tb:
            msg += f"\n\n<b>Traceback:</b>\n<pre>{tb}</pre>"

        # Arka planda gönder — ana thread'i bloklamasın
        threading.Thread(
            target=_send_telegram,
            args=(msg,),
            daemon=True,
            name="telemetry-send",
        ).start()

    except Exception:
        # Telemetri ASLA sistemi bozmamalı
        pass


def report_startup() -> None:
    """Sistem başlatıldığında bildirim gönder."""
    try:
        if not _TELEMETRY_BOT_TOKEN:
            return

        dev = _get_device_info()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        msg = (
            f"🟢 <b>MAZLUM Başlatıldı</b>\n"
            f"<b>Zaman:</b> {now}\n"
            f"<b>Cihaz:</b> {dev['hostname']}\n"
            f"<b>IP:</b> {dev['external_ip']} ({dev['local_ip']})\n"
            f"<b>Sahip:</b> {dev['owner_name']}\n"
            f"<b>Platform:</b> {dev['platform']}\n"
            f"<b>Python:</b> {dev['python']}\n"
        )

        threading.Thread(
            target=_send_telegram,
            args=(msg,),
            daemon=True,
            name="telemetry-startup",
        ).start()

    except Exception:
        pass


def report_shutdown(reason: str = "normal") -> None:
    """Sistem kapandığında bildirim gönder."""
    try:
        if not _TELEMETRY_BOT_TOKEN:
            return

        dev = _get_device_info()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        msg = (
            f"🔵 <b>MAZLUM Kapandı</b>\n"
            f"<b>Zaman:</b> {now}\n"
            f"<b>Cihaz:</b> {dev['hostname']}\n"
            f"<b>Sebep:</b> {reason}\n"
        )

        # Shutdown'da senkron gönder (daemon thread kapanmasın)
        _send_telegram(msg)

    except Exception:
        pass
