"""
Telegram search tools for Brain.
Bridges async TelegramMonitor to sync Brain tool registry.
"""
import asyncio
import logging
from typing import Optional

from seriai.tools.registry import ToolDef, ToolRegistry

log = logging.getLogger("seriai.tools.telegram")

# Global reference — set by main.py after TelegramMonitor connects
_monitor = None


def set_telegram_monitor(monitor):
    """Set the TelegramMonitor instance. Called from main.py."""
    global _monitor
    _monitor = monitor
    log.info("Telegram tools: monitor bağlandı.")


def _run_async(coro, timeout=15.0):
    """Run async coroutine from sync context (Brain tool handler)."""
    if not _monitor or not _monitor.is_connected:
        return {"error": "Telegram monitör aktif değil."}
    try:
        loop = _monitor._client.loop
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    except TimeoutError:
        return {"error": "Telegram araması zaman aşımına uğradı."}
    except Exception as e:
        log.error(f"Telegram tool error: {e}")
        return {"error": f"Telegram hatası: {e}"}


def search_telegram(query: str, chat_name: str = "", limit: int = 20) -> str:
    """Search messages across all Telegram chats."""
    chat_id = None
    if chat_name:
        found = _run_async(_monitor.find_chat(chat_name))
        if isinstance(found, dict) and "error" in found:
            return found["error"]
        if found:
            chat_id = found["id"]

    msgs = _run_async(_monitor.search_messages(query=query, limit=limit, chat_id=chat_id))
    if isinstance(msgs, dict) and "error" in msgs:
        return msgs["error"]
    if not msgs:
        return f"'{query}' ile ilgili mesaj bulunamadı."

    lines = [f"'{query}' araması — {len(msgs)} sonuç:"]
    for m in msgs:
        direction = "Sen" if m.get("is_outgoing") else m.get("sender", "?")
        chat = m.get("chat_name", "?")
        date = m.get("date", "")
        text = m.get("text", "")[:150]
        lines.append(f"- [{direction}] ({chat}) [{date}]: {text}")
    return "\n".join(lines)


def list_telegram_chats(filter: str = "", limit: int = 30) -> str:
    """List Telegram chats/groups."""
    chats = _run_async(_monitor.list_dialogs(limit=limit, filter_type=filter or None))
    if isinstance(chats, dict) and "error" in chats:
        return chats["error"]
    if not chats:
        return "Sohbet bulunamadı."

    lines = [f"{len(chats)} sohbet:"]
    for c in chats:
        name = c.get("name", "?")
        ctype = c.get("type", "?")
        unread = c.get("unread", 0)
        tag = f" [{unread} okunmamış]" if unread else ""
        lines.append(f"- {name} ({ctype}){tag}")
    return "\n".join(lines)


def get_chat_messages(chat_name: str, limit: int = 15) -> str:
    """Get recent messages from a specific chat."""
    msgs = _run_async(_monitor.get_chat_messages(chat_name=chat_name, limit=limit))
    if isinstance(msgs, dict) and "error" in msgs:
        return msgs["error"]
    if not msgs:
        return f"'{chat_name}' sohbetinde mesaj bulunamadı."

    lines = [f"'{chat_name}' — son {len(msgs)} mesaj:"]
    for m in msgs:
        sender = m.get("sender", "?")
        date = m.get("date", "")
        text = m.get("text", "")[:200]
        lines.append(f"- [{sender}] [{date}]: {text}")
    return "\n".join(lines)


def register_telegram_tools(registry: ToolRegistry):
    """Register Telegram tools in Brain's tool registry."""
    registry.register(ToolDef(
        name="search_telegram",
        description="Telegram mesajlarında arama yap. Tüm gruplarda veya belirli bir sohbette mesaj ara.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Aranacak kelime veya cümle"},
                "chat_name": {"type": "string", "description": "Belirli bir sohbet/grup adı (opsiyonel)"},
                "limit": {"type": "integer", "description": "Maksimum sonuç sayısı (varsayılan: 20)"},
            },
            "required": ["query"],
        },
        handler=search_telegram,
    ))

    registry.register(ToolDef(
        name="list_telegram_chats",
        description="Telegram sohbet/grup listesini getir.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Filtre: 'group', 'channel', 'user' (opsiyonel)"},
                "limit": {"type": "integer", "description": "Maksimum sonuç (varsayılan: 30)"},
            },
            "required": [],
        },
        handler=list_telegram_chats,
    ))

    registry.register(ToolDef(
        name="get_chat_messages",
        description="Belirli bir Telegram sohbetinin son mesajlarını getir.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "chat_name": {"type": "string", "description": "Sohbet/grup adı"},
                "limit": {"type": "integer", "description": "Kaç mesaj getirilsin (varsayılan: 15)"},
            },
            "required": ["chat_name"],
        },
        handler=get_chat_messages,
    ))

    log.info("Telegram tools registered: 3 tools (search, list, messages)")
