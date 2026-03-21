"""
FastAPI web server for MAZLUM.
WebSocket with real-time state broadcasting: thinking, speaking, tools, transcript.
"""
import logging
import json
import time
import asyncio
from pathlib import Path
from typing import Set

log = logging.getLogger("seriai.interface.web")

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Connected WebSocket clients
_clients: Set = set()


async def broadcast(event_type: str, data: dict):
    """Broadcast event to all connected WebSocket clients."""
    if not _clients:
        return
    msg = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_text(msg)
        except Exception as e:
            log.debug(f"Broadcast send failed: {e}")
            dead.add(ws)
    if dead:
        _clients.difference_update(dead)


def create_app(brain, config):
    """Create FastAPI app with WebSocket support."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="MAZLUM", version="1.0.0")

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def index():
        index_path = _TEMPLATES_DIR / "index.html"
        if index_path.exists():
            return HTMLResponse(index_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>MAZLUM</h1><p>Web UI dosyaları bulunamadı.</p>")

    @app.get("/health")
    async def health():
        tools_list = brain.tools.list_tools()
        return JSONResponse({
            "status": "ok",
            "tools": tools_list,
            "tool_count": len(tools_list),
            "model": config.models.cognition_model,
            "light_model": config.models.light_model,
        })

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        _clients.add(ws)
        log.info(f"WebSocket client connected. Total: {len(_clients)}")

        # Send initial state
        tools_list = brain.tools.list_tools()
        # Voice/Telegram tools (Gemini tarafında tanımlı, brain'de değil)
        voice_tools = [
            'check_telegram_mentions', 'telegram_summary', 'search_telegram',
            'list_telegram_chats', 'read_telegram_chat', 'reply_telegram',
            'seriai_brain', 'self_shutdown',
        ]
        all_tools = list(set(tools_list + voice_tools))
        has_db = 'db_query' in tools_list
        await ws.send_text(json.dumps({
            "type": "state_sync",
            "data": {
                "tools": all_tools,
                "model": config.models.cognition_model,
                "light_model": config.models.light_model,
                "connected": True,
                "voice_active": config.enable_voice,
                "telegram_active": config.enable_telegram,
                "db_active": has_db,
            }
        }, ensure_ascii=False))

        try:
            while True:
                data = await ws.receive_text()
                if len(data) > 10240:
                    await ws.send_text(json.dumps({"type": "error", "data": {"text": "Mesaj çok uzun."}}))
                    continue

                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue

                action = msg.get("action", "message")

                if action == "message":
                    user_text = msg.get("text", "").strip()
                    if not user_text:
                        continue

                    # Broadcast: user message
                    await broadcast("transcript", {"role": "user", "text": user_text})

                    # Broadcast: thinking started
                    await broadcast("thinking", {"active": True, "text": user_text})

                    try:
                        loop = asyncio.get_running_loop()
                        t0 = time.time()

                        response = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: brain.process(user_text, context={"source": "web"})
                            ),
                            timeout=60.0,
                        )

                        elapsed = int((time.time() - t0) * 1000)

                        # Broadcast: thinking done
                        await broadcast("thinking", {"active": False})

                        # Broadcast: tool usage
                        if response.tools_used:
                            for tool in response.tools_used:
                                await broadcast("tool_state", {"name": tool, "state": "done"})

                        # Broadcast: AI response
                        await broadcast("transcript", {
                            "role": "ai",
                            "text": response.text,
                            "model": response.model_used,
                            "domain": response.domain,
                            "tokens": response.input_tokens + response.output_tokens,
                            "latency_ms": elapsed,
                            "tools_used": response.tools_used,
                        })

                        # Also send as direct response for backward compat
                        await ws.send_text(json.dumps({
                            "type": "response",
                            "data": {
                                "text": response.text,
                                "model": response.model_used,
                                "domain": response.domain,
                                "tokens": response.input_tokens + response.output_tokens,
                                "latency_ms": elapsed,
                                "tools_used": response.tools_used,
                            }
                        }, ensure_ascii=False))

                    except asyncio.TimeoutError:
                        log.warning(f"Brain timeout (60s) for: {user_text[:50]}")
                        await broadcast("thinking", {"active": False})
                        error_text = "İstek zaman aşımına uğradı (60s). Tekrar deneyin."
                        await broadcast("transcript", {"role": "system", "text": error_text})
                        await ws.send_text(json.dumps({
                            "type": "response",
                            "data": {"text": error_text, "error": True}
                        }, ensure_ascii=False))
                    except Exception as e:
                        log.error(f"Brain error: {e}")
                        await broadcast("thinking", {"active": False})
                        error_text = f"Hata: {e}"
                        await broadcast("transcript", {"role": "system", "text": error_text})
                        await ws.send_text(json.dumps({
                            "type": "response",
                            "data": {"text": error_text, "error": True}
                        }, ensure_ascii=False))

                elif action == "request_state":
                    tools_list = brain.tools.list_tools()
                    await ws.send_text(json.dumps({
                        "type": "state_sync",
                        "data": {"tools": tools_list, "connected": True}
                    }))

        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.error(f"WebSocket error: {e}")
        finally:
            _clients.discard(ws)
            log.info(f"WebSocket client disconnected. Total: {len(_clients)}")

    return app
