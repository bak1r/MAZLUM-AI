"""
MAZLUM - Main entry point.
Asyncio-based supervisor: tek komutla tüm servisler başlar.
Varsayılan mod: all (CLI + Web + Telegram paralel çalışır)
"""
import os
import sys
import signal
import logging
import asyncio
import argparse
from pathlib import Path

# Suppress harmless Python 3.9 deprecation warnings
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")
warnings.filterwarnings("ignore", message=".*urllib3.*")

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env before anything else
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

from seriai.config.settings import load_config, MEMORY_DIR, BASE_DIR
from seriai.cognition.brain import Brain
from seriai.memory.manager import MemoryManager
from seriai.tools.registry import ToolRegistry
from seriai.tools.common import register_common_tools
from seriai.tools.desktop import register_desktop_tools
from seriai.tools.documents import register_document_tools
from seriai.tools.vision import register_vision_tools


def setup_logging(level: str = "INFO", mode: str = "all"):
    """Setup logging. In 'all' mode with CLI, logs go to file so terminal stays clean."""
    handlers = []

    if mode == "all":
        # all modda loglar dosyaya gitsin, terminal CLI için temiz kalsın
        log_file = BASE_DIR / "data" / "seriai.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
        ))
        handlers.append(file_handler)
        # Sadece kritik hatalar terminale gelsin
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
        handlers.append(console_handler)
    else:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
        ))
        handlers.append(console_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )
    # Gürültülü kütüphane loglarını sustur
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)


def init_system(mode: str = "all"):
    """Initialize config, memory, tools, brain."""
    config = load_config()
    setup_logging(config.log_level, mode=mode)
    log = logging.getLogger("seriai")

    log.info("MAZLUM starting...")
    log.info(f"Platform: {sys.platform}")
    log.info(f"Cognition: {config.models.cognition_model}")
    log.info(f"Light: {config.models.light_model}")

    memory = MemoryManager(MEMORY_DIR)
    tools = ToolRegistry()
    register_common_tools(tools)
    register_desktop_tools(tools)
    register_document_tools(tools)
    register_vision_tools(tools)
    from seriai.tools.telegram import register_telegram_tools
    register_telegram_tools(tools)

    db = None
    if config.enable_db_intelligence:
        from seriai.tools.db.readonly import ReadOnlyDB
        from seriai.tools.db.tools import create_db_tools
        db = ReadOnlyDB(config)
        if db.connect():
            for tool_def in create_db_tools(db):
                tools.register(tool_def)
            log.info("Database tools registered.")
        else:
            log.warning("Database connection failed. DB tools not available.")
            db = None

    log.info(f"Tools registered: {tools.count}")
    brain = Brain(config=config, memory=memory, tools=tools)
    return config, memory, brain, db, log


async def run_telegram(brain, config, log):
    """Run Telegram bot as async task."""
    if not config.enable_telegram:
        return
    from seriai.interface.telegram.bot import TelegramBot
    bot = TelegramBot(config, brain)
    await bot.start()
    log.info("Telegram bot started.")
    # Keep alive until cancelled
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        await bot.stop()
        log.info("Telegram bot stopped.")


async def run_web(brain, config, log):
    """Run web server as async task."""
    if not config.enable_web_ui:
        return
    import uvicorn
    from seriai.interface.web.server import create_app
    app = create_app(brain, config)
    uv_config = uvicorn.Config(
        app, host="127.0.0.1", port=config.web_port, log_level="warning"
    )
    server = uvicorn.Server(uv_config)
    url = f"http://127.0.0.1:{config.web_port}"
    log.info(f"Web UI: {url}")
    # Tarayıcıda otomatik aç
    import webbrowser
    webbrowser.open(url)
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        log.info("Web server stopped.")


async def run_cli(brain, log):
    """Run CLI in async context using thread executor."""
    from seriai.interface.chat.cli import run_cli as _run_cli
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_cli, brain)


async def run_alert_monitor(db, config, log):
    """Run callback failure alert monitor as async task."""
    if not db or not config.enable_telegram:
        return
    alert_chat_ids_str = os.getenv("SERIAI_ALERT_CHAT_IDS", "")
    if not alert_chat_ids_str:
        # Fall back to allowed user IDs
        alert_chat_ids = config.telegram.allowed_user_ids
    else:
        alert_chat_ids = []
        for x in alert_chat_ids_str.split(","):
            x = x.strip()
            if x:
                try:
                    alert_chat_ids.append(int(x))
                except ValueError:
                    log.warning(f"Invalid alert chat ID ignored: {x}")

    if not alert_chat_ids:
        log.warning("No alert chat IDs configured. Callback alerting disabled.")
        return

    from seriai.monitoring.alerts import CallbackAlertMonitor
    interval = int(os.getenv("SERIAI_ALERT_INTERVAL", "300") or "300")
    threshold = int(os.getenv("SERIAI_ALERT_THRESHOLD", "10") or "10")
    monitor = CallbackAlertMonitor(
        db=db,
        telegram_bot_token=config.telegram.bot_token,
        alert_chat_ids=alert_chat_ids,
        check_interval=interval,
        threshold=threshold,
    )
    await monitor.start()


# Global referanslar — servisler arası bağlantılar
_telegram_monitor_instance = None
_voice_engine_instance = None


async def run_voice(brain, config, log):
    """Run voice engine as async task."""
    if not config.enable_voice:
        return
    from seriai.io.voice import VoiceEngine

    global _telegram_monitor_instance, _voice_engine_instance
    voice = VoiceEngine(
        config=config, brain=brain,
        telegram_monitor=_telegram_monitor_instance,
    )
    _voice_engine_instance = voice

    if not voice.is_available():
        log.warning("pyaudio yüklü değil. Voice başlatılamıyor.")
        return

    voice.set_log_handler(lambda msg: log.info(f"[Voice] {msg}"))

    # Web UI'a voice event'leri broadcast et
    try:
        from seriai.interface.web.server import broadcast
        voice.set_broadcast(broadcast)
        log.info("Voice → Web UI broadcast bağlandı.")
    except Exception:
        log.warning("Voice → Web UI broadcast bağlanamadı.")

    # Telegram mention → sesli bildirim (clean callback, monkey-patch değil)
    if _telegram_monitor_instance and _telegram_monitor_instance.is_connected:
        async def _on_mention(sender_name, chat_name, text_preview, reason):
            notification = (
                f"[BİLDİRİM] Telegram'da {sender_name} sizi {reason}. "
                f"Sohbet: '{chat_name}'. Mesaj: \"{text_preview}\". "
                f"Kullanıcıya kısa sesli bildir."
            )
            await voice.inject_notification(notification)

        _telegram_monitor_instance.set_on_mention(_on_mention)
        log.info("Telegram mention → Sesli bildirim bağlandı.")

    log.info("Voice engine başlatılıyor...")
    try:
        await voice.run_async()
    except asyncio.CancelledError:
        voice.stop()
        log.info("Voice engine durduruldu.")


async def run_proactive_monitor(db, config, log):
    """Run proactive DB monitoring as async task."""
    if not db or not config.monitoring.enable_proactive:
        return

    alert_chat_ids_str = os.getenv("SERIAI_ALERT_CHAT_IDS", "")
    if not alert_chat_ids_str:
        alert_chat_ids = config.telegram.allowed_user_ids
    else:
        alert_chat_ids = []
        for x in alert_chat_ids_str.split(","):
            x = x.strip()
            if x:
                try:
                    alert_chat_ids.append(int(x))
                except ValueError:
                    pass

    from seriai.monitoring.proactive import ProactiveMonitor
    monitor = ProactiveMonitor(
        db=db,
        config=config,
        voice_engine=_voice_engine_instance,
        bot_token=config.telegram.bot_token if config.enable_telegram else "",
        alert_chat_ids=alert_chat_ids,
    )

    # Voice engine referansı sonradan set edilebilir
    async def _monitor_with_delayed_voice():
        # Voice engine başlayana kadar bekle (max 15s)
        for _ in range(15):
            if _voice_engine_instance:
                monitor.set_voice_engine(_voice_engine_instance)
                break
            await asyncio.sleep(1)
        await monitor.start()

    await _monitor_with_delayed_voice()


async def supervisor(mode: str, brain, config, memory, db, log):
    """Main async supervisor — runs services based on mode.

    all  = Web UI (sesli) + Telegram + Alert monitor
    cli  = Terminal sohbet (sadece geliştirici için)
    web  = Sadece Web UI
    telegram = Sadece Telegram
    voice = Sadece ses modu
    """
    tasks = []

    if mode in ("all", "web"):
        t = asyncio.create_task(run_web(brain, config, log))
        t.set_name("web_server")
        tasks.append(t)

    if mode in ("all", "telegram"):
        t = asyncio.create_task(run_telegram(brain, config, log))
        t.set_name("telegram_bot")
        tasks.append(t)

    # Alert monitor
    if mode == "all" and db and config.enable_telegram:
        t = asyncio.create_task(run_alert_monitor(db, config, log))
        t.set_name("alert_monitor")
        tasks.append(t)

    # Telegram User API monitor — voice'dan önce başlat
    global _telegram_monitor_instance
    if mode in ("all", "web", "voice"):
        try:
            from seriai.monitoring.telegram_monitor import TelegramMonitor
            tg_mon = TelegramMonitor()
            if tg_mon._is_configured():
                # Web UI broadcast bağla
                try:
                    from seriai.interface.web.server import broadcast as web_broadcast
                    tg_mon.set_broadcast(web_broadcast)
                except Exception:
                    pass
                await tg_mon.start()
                if tg_mon.is_connected:
                    _telegram_monitor_instance = tg_mon
                    # Brain tool'larına Telegram monitor bağla
                    from seriai.tools.telegram import set_telegram_monitor
                    set_telegram_monitor(tg_mon)
                    log.info("Telegram User API monitör aktif.")

                    async def _tg_monitor_loop():
                        try:
                            while tg_mon.is_connected:
                                await asyncio.sleep(1)
                        except asyncio.CancelledError:
                            await tg_mon.stop()

                    t = asyncio.create_task(_tg_monitor_loop())
                    t.set_name("telegram_monitor")
                    tasks.append(t)
        except Exception as e:
            log.warning(f"Telegram monitör başlatılamadı: {e}")

    # Voice
    if mode == "voice" or (mode == "all" and config.enable_voice):
        t = asyncio.create_task(run_voice(brain, config, log))
        t.set_name("voice_engine")
        tasks.append(t)

    # Proactive DB monitor — voice'dan sonra başlat (voice referansını bekler)
    if mode == "all" and db and config.monitoring.enable_proactive:
        t = asyncio.create_task(run_proactive_monitor(db, config, log))
        t.set_name("proactive_monitor")
        tasks.append(t)

    # CLI — sadece "cli" modunda, "all" modda yok (web UI var)
    if mode == "cli":
        t = asyncio.create_task(run_cli(brain, log))
        t.set_name("cli_task")
        tasks.append(t)

    if not tasks:
        log.error(f"No services to start for mode: {mode}")
        return

    try:
        # CLI modu: CLI bitince kapat. Diğerleri: sonsuza kadar çalış.
        if mode == "cli":
            await tasks[0]
        else:
            # Servisler sonsuza kadar çalışır — bir servisin çökmesi diğerlerini durdurmamalı
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    log.error(f"Service task {tasks[i].get_name()} crashed: {r}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error(f"Supervisor error: {e}")
    finally:
        # Cancel remaining tasks
        for t in tasks:
            if not t.done():
                t.cancel()
        # Wait with timeout — don't hang on stuck tasks
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=3)
            for t in pending:
                log.warning(f"Task {t.get_name()} did not exit in time, abandoning.")
        # Cleanup
        if db:
            db.close()
        memory.save()
        log.info("MAZLUM stopped.")


def main():
    parser = argparse.ArgumentParser(description="MAZLUM")
    parser.add_argument(
        "--mode",
        choices=["all", "cli", "web", "telegram", "voice"],
        default=None,
        help="Çalışma modu (varsayılan: .env'deki SERIAI_MODE veya 'all')",
    )
    args = parser.parse_args()
    mode = args.mode or os.getenv("SERIAI_MODE", "all").lower()

    config, memory, brain, db, log = init_system(mode=mode)

    log.info(f"Mode: {mode}")

    # Telemetry — uzaktan hata izleme
    from seriai.monitoring.telemetry import report_startup, report_shutdown
    report_startup()

    # Startup bilgisini her zaman terminale yazdır
    print(f"\n{'='*50}")
    print(f"  MAZLUM v1.0")
    print(f"  Beyin: {config.models.cognition_model}")
    print(f"  Araçlar: {brain.tools.count} kayıtlı")
    if config.enable_web_ui and mode in ("all", "web"):
        print(f"  Web UI: http://127.0.0.1:{config.web_port}")
    if config.enable_telegram and mode in ("all", "telegram"):
        print(f"  Telegram: aktif")
    if db:
        print(f"  Veritabanı: bağlı")
    if os.getenv("SERIAI_TG_API_ID", "").strip():
        print(f"  Telegram Monitör: yapılandırılmış")
    if db and config.monitoring.enable_proactive:
        print(f"  Proaktif İzleme: aktif ({config.monitoring.check_interval_sec}s)")
    print(f"  Log: data/seriai.log")
    print(f"{'='*50}")
    if mode != "cli":
        print(f"  Ctrl+C ile kapat")
    print()

    # Graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Thread pool: Voice + Telegram + Web + Proactive + Alert hepsi executor kullanıyor
    import concurrent.futures
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="seriai")
    loop.set_default_executor(executor)

    _shutdown_count = 0

    def shutdown_handler(sig, frame):
        nonlocal _shutdown_count
        _shutdown_count += 1
        if _shutdown_count == 1:
            print("\n  Kapatılıyor...")
            for task in list(asyncio.all_tasks(loop)):
                task.cancel()
        else:
            # İkinci Ctrl+C — force quit
            print("\n  Zorla kapatılıyor!")
            os._exit(1)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        loop.run_until_complete(supervisor(mode, brain, config, memory, db, log))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Pending task'ları temizle
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=3,
                ))
            except (asyncio.TimeoutError, asyncio.CancelledError, KeyboardInterrupt):
                pass
        loop.close()
        report_shutdown("normal")
        print("  MAZLUM kapatıldı.")


if __name__ == "__main__":
    main()
