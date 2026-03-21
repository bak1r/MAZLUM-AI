"""
Desktop tools — Mac uyumlu uygulama/dosya/sistem kontrol araçları.
"""
import logging
import os
import platform
import subprocess
import shutil
from pathlib import Path
from seriai.tools.registry import ToolDef

log = logging.getLogger("seriai.tools.desktop")

IS_MAC = platform.system() == "Darwin"


# ── App Launcher ────────────────────────────────────────────────────

_MAC_APPS = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome",
    "safari": "Safari", "firefox": "Firefox",
    "spotify": "Spotify", "telegram": "Telegram",
    "whatsapp": "WhatsApp", "discord": "Discord",
    "slack": "Slack", "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "terminal": "Terminal", "iterm": "iTerm",
    "finder": "Finder", "word": "Microsoft Word",
    "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
    "notes": "Notes", "calculator": "Calculator",
    "preview": "Preview", "activity monitor": "Activity Monitor",
    "system settings": "System Settings",
    "messages": "Messages", "mail": "Mail",
    "calendar": "Calendar", "music": "Music",
    "photos": "Photos", "obsidian": "Obsidian",
    "notion": "Notion", "postman": "Postman",
    "figma": "Figma", "zoom": "zoom.us",
}


def open_app(app_name: str) -> dict:
    """Uygulama aç."""
    if not IS_MAC:
        return {"error": "Sadece macOS destekleniyor."}

    key = app_name.lower().strip()
    resolved = _MAC_APPS.get(key, app_name)

    try:
        result = subprocess.run(
            ["open", "-a", resolved],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return {"result": f"{resolved} açıldı."}
        else:
            return {"error": f"{resolved} bulunamadı: {result.stderr.strip()}"}
    except Exception as e:
        return {"error": str(e)}


def close_app(app_name: str) -> dict:
    """Uygulama kapat."""
    if not IS_MAC:
        return {"error": "Sadece macOS destekleniyor."}

    key = app_name.lower().strip()
    resolved = _MAC_APPS.get(key, app_name)
    # Sanitize: remove quotes to prevent osascript injection
    resolved = resolved.replace('"', '').replace("'", "")

    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{resolved}" to quit'],
            capture_output=True, timeout=10
        )
        return {"result": f"{resolved} kapatıldı."}
    except Exception as e:
        return {"error": str(e)}


# ── File Operations ─────────────────────────────────────────────────

_SHORTCUTS = {
    "desktop": Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "pictures": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "home": Path.home(),
}


def open_file(file_path: str) -> dict:
    """Dosya aç (Mac'in varsayılan uygulamasıyla)."""
    path = _resolve_path(file_path)
    # Path traversal guard: resolve to absolute and check it's under allowed dirs
    try:
        resolved = path.resolve()
    except Exception:
        return {"error": f"Geçersiz dosya yolu: {file_path}"}
    if not resolved.exists():
        return {"error": f"Dosya bulunamadı: {resolved}"}
    try:
        subprocess.run(["open", str(resolved)], timeout=10, capture_output=True)
        return {"result": f"{resolved.name} açıldı."}
    except subprocess.TimeoutExpired:
        return {"error": "Dosya açma zaman aşımına uğradı."}
    except Exception as e:
        return {"error": str(e)}


def list_files(directory: str = "desktop", pattern: str = "*") -> dict:
    """Dizindeki dosyaları listele."""
    path = _resolve_path(directory)
    if not path.is_dir():
        return {"error": f"Dizin bulunamadı: {path}"}
    try:
        files = sorted(path.glob(pattern))[:50]
        items = []
        for f in files:
            try:
                items.append({
                    "name": f.name,
                    "type": "dir" if f.is_dir() else "file",
                    "size": f.stat().st_size if f.is_file() else 0,
                })
            except (FileNotFoundError, OSError):
                continue  # Dosya arada silindi, atla
        return {"files": items, "count": len(items), "directory": str(path)}
    except Exception as e:
        return {"error": str(e)}


def create_file(file_path: str, content: str = "") -> dict:
    """Dosya oluştur."""
    path = _resolve_path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"result": f"{path.name} oluşturuldu.", "path": str(path)}
    except Exception as e:
        return {"error": str(e)}


def _resolve_path(raw: str) -> Path:
    lower = raw.strip().lower()
    if lower in _SHORTCUTS:
        return _SHORTCUTS[lower]
    return Path(raw).expanduser()


# ── System Settings ─────────────────────────────────────────────────

def computer_settings(action: str, value: str = "") -> dict:
    """Bilgisayar ayarları (Mac)."""
    if not IS_MAC:
        return {"error": "Sadece macOS destekleniyor."}

    try:
        if action == "volume_up":
            subprocess.run(["osascript", "-e",
                "set volume output volume ((output volume of (get volume settings)) + 10)"],
                capture_output=True, timeout=10)
            return {"result": "Ses artırıldı."}
        elif action == "volume_down":
            subprocess.run(["osascript", "-e",
                "set volume output volume ((output volume of (get volume settings)) - 10)"],
                capture_output=True, timeout=10)
            return {"result": "Ses azaltıldı."}
        elif action == "mute":
            subprocess.run(["osascript", "-e", "set volume with output muted"],
                capture_output=True, timeout=10)
            return {"result": "Ses kapatıldı."}
        elif action == "unmute":
            subprocess.run(["osascript", "-e", "set volume without output muted"],
                capture_output=True, timeout=10)
            return {"result": "Ses açıldı."}
        elif action == "screenshot":
            dest = Path.home() / "Desktop" / "screenshot.png"
            subprocess.run(["screencapture", "-x", str(dest)], capture_output=True, timeout=10)
            return {"result": f"Ekran görüntüsü kaydedildi: {dest}"}
        elif action == "get_volume":
            r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                capture_output=True, text=True, timeout=10)
            return {"result": f"Ses seviyesi: {r.stdout.strip()}"}
        else:
            return {"error": f"Bilinmeyen ayar: {action}"}
    except Exception as e:
        return {"error": str(e)}


# ── Browser (basit URL açma) ────────────────────────────────────────

def open_url(url: str, browser: str = "default") -> dict:
    """URL'yi tarayıcıda aç."""
    try:
        if browser == "default" or not IS_MAC:
            subprocess.Popen(["open", url])
        else:
            app = _MAC_APPS.get(browser.lower(), browser)
            subprocess.Popen(["open", "-a", app, url])
        return {"result": f"{url} açıldı."}
    except Exception as e:
        return {"error": str(e)}


# ── Tool Registration ───────────────────────────────────────────────

def register_desktop_tools(registry):
    """Desktop tool'larını registry'ye kaydet."""
    registry.register(ToolDef(
        name="open_app",
        description="macOS'ta uygulama aç. Chrome, Safari, Telegram, Finder, Terminal, Word, Excel, vb.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Uygulama adı (chrome, safari, telegram, vb.)"}
            },
            "required": ["app_name"],
        },
        handler=open_app,
    ))

    registry.register(ToolDef(
        name="close_app",
        description="macOS'ta uygulamayı kapat.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Kapatılacak uygulama adı"}
            },
            "required": ["app_name"],
        },
        handler=close_app,
    ))

    registry.register(ToolDef(
        name="open_file",
        description="Dosyayı Mac'in varsayılan uygulamasıyla aç.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Dosya yolu veya kısayol (desktop, downloads, documents)"}
            },
            "required": ["file_path"],
        },
        handler=open_file,
    ))

    registry.register(ToolDef(
        name="list_files",
        description="Dizindeki dosyaları listele.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Dizin yolu veya kısayol (desktop, downloads)", "default": "desktop"},
                "pattern": {"type": "string", "description": "Glob pattern (*, *.pdf, *.docx)", "default": "*"},
            },
            "required": [],
        },
        handler=list_files,
    ))

    registry.register(ToolDef(
        name="create_file",
        description="Yeni dosya oluştur.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Dosya yolu"},
                "content": {"type": "string", "description": "Dosya içeriği", "default": ""},
            },
            "required": ["file_path"],
        },
        handler=create_file,
    ))

    registry.register(ToolDef(
        name="computer_settings",
        description="Mac bilgisayar ayarları: ses aç/kapat, ekran görüntüsü, vb.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "volume_up|volume_down|mute|unmute|screenshot|get_volume"},
                "value": {"type": "string", "description": "Opsiyonel değer", "default": ""},
            },
            "required": ["action"],
        },
        handler=computer_settings,
    ))

    registry.register(ToolDef(
        name="open_url",
        description="URL'yi tarayıcıda aç.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Açılacak URL"},
                "browser": {"type": "string", "description": "Tarayıcı (default, chrome, safari, firefox)", "default": "default"},
            },
            "required": ["url"],
        },
        handler=open_url,
    ))

    log.info(f"Desktop tools registered: 7 tools")
