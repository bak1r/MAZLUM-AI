"""
Telegram Bot API integration.
Production-primary method: native Bot API, not GUI automation.
Replaces the old PyAutoGUI + clipboard + window focus approach.

Features:
- Incoming message processing
- Auth / identity mapping
- Rate control
- Command handling
- CRM/DB tool access via brain
- Escalation / human handoff
- Fail-safe timeouts
"""
import logging
import asyncio
from typing import Optional

log = logging.getLogger("seriai.interface.telegram")


class TelegramBot:
    """
    Telegram Bot API client.
    Uses python-telegram-bot library for reliable message handling.
    """

    def __init__(self, config, brain):
        self.config = config
        self.brain = brain
        self._app = None
        self._running = False

    async def start(self):
        """Start the Telegram bot."""
        if not self.config.telegram.bot_token:
            log.warning("Telegram bot token not configured. Skipping.")
            return

        try:
            from telegram import Update
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
            )

            self._app = (
                Application.builder()
                .token(self.config.telegram.bot_token)
                .build()
            )

            # Command handlers
            self._app.add_handler(CommandHandler("start", self._cmd_start))
            self._app.add_handler(CommandHandler("help", self._cmd_help))
            self._app.add_handler(CommandHandler("status", self._cmd_status))

            # Message handler (text messages)
            self._app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
            )

            # Error handler
            self._app.add_error_handler(self._handle_error)

            self._running = True

            if self.config.telegram.use_polling:
                log.info("Telegram bot starting (polling mode)...")
                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(drop_pending_updates=True)
            else:
                # Webhook mode not yet implemented — fall back to polling with warning
                log.warning("Webhook mode henüz implement edilmedi. Polling moduna düşülüyor.")
                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(drop_pending_updates=True)

        except ImportError:
            log.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
        except Exception as e:
            log.error(f"Telegram bot start failed: {e}")

    async def stop(self):
        """Stop the Telegram bot gracefully."""
        if self._app and self._running:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                self._running = False
                log.info("Telegram bot stopped.")
            except Exception as e:
                log.error(f"Telegram stop error: {e}")

    def _is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized."""
        if not self.config.telegram.allowed_user_ids:
            return True  # no whitelist = allow all
        return user_id in self.config.telegram.allowed_user_ids

    async def _cmd_start(self, update, context):
        """Handle /start command."""
        if not update.message:
            return
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Yetkiniz bulunmuyor.")
            return
        await update.message.reply_text(
            "MAZLUM aktif. Sorularınızı yazabilirsiniz.\n"
            "/help - Komutlar\n"
            "/status - Sistem durumu"
        )

    async def _cmd_help(self, update, context):
        """Handle /help command."""
        if not update.message:
            return
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Yetkiniz bulunmuyor.")
            return
        await update.message.reply_text(
            "Komutlar:\n"
            "/start - Başlat\n"
            "/help - Yardım\n"
            "/status - Durum\n\n"
            "Herhangi bir metin mesajı yazarak soru sorabilirsiniz."
        )

    async def _cmd_status(self, update, context):
        """Handle /status command."""
        if not update.message:
            return
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Yetkiniz bulunmuyor.")
            return
        mem_stats = self.brain.memory.stats()
        tools = self.brain.tools.list_tools()
        await update.message.reply_text(
            f"MAZLUM Durum:\n"
            f"Araç sayısı: {len(tools)}\n"
            f"Bellek: {sum(mem_stats.values())} kayıt\n"
            f"Model: {self.brain.config.models.cognition_model}"
        )

    async def _handle_message(self, update, context):
        """Handle incoming text messages."""
        if not update.message:
            return  # Edited message, channel post, etc.

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Yetkiniz bulunmuyor.")
            return

        text = update.message.text
        if not text:
            return

        log.info(f"Telegram msg from {user_id}: {text[:80]}")

        try:
            from telegram.constants import ChatAction

            # Show "typing..." immediately
            await update.message.chat.send_action(ChatAction.TYPING)

            # Start periodic typing indicator (expires every ~5s, so resend every 4s)
            typing_active = True

            async def keep_typing():
                while typing_active:
                    await asyncio.sleep(4)
                    if typing_active:
                        try:
                            await update.message.chat.send_action(ChatAction.TYPING)
                        except Exception:
                            break

            typing_task = asyncio.create_task(keep_typing())

            try:
                # Progress callback — Brain her tool round'unda ara sonuç gönderir
                loop = asyncio.get_running_loop()
                progress_queue = asyncio.Queue()

                def on_progress(text):
                    """Brain'den gelen ara sonuçları queue'ya ekle (thread-safe)."""
                    loop.call_soon_threadsafe(progress_queue.put_nowait, text)

                async def send_progress():
                    """Queue'daki ara sonuçları Telegram'a gönder."""
                    while typing_active:
                        try:
                            text = await asyncio.wait_for(progress_queue.get(), timeout=2.0)
                            if text and text.strip():
                                max_len = self.config.telegram.max_message_length
                                msg = text.strip()[:max_len]
                                try:
                                    await update.message.reply_text(msg)
                                    await update.message.chat.send_action(ChatAction.TYPING)
                                except Exception:
                                    pass
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            break

                progress_task = asyncio.create_task(send_progress())

                # Process through brain (with timeout)
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self.brain.process(
                            user_text=text,
                            context={
                                "source": "telegram",
                                "user_id": user_id,
                                "username": update.effective_user.username or "",
                            },
                            progress_callback=on_progress,
                        ),
                    ),
                    timeout=300.0,
                )
            finally:
                # Stop typing + progress
                typing_active = False
                typing_task.cancel()
                progress_task.cancel()
                try:
                    await typing_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await progress_task
                except (asyncio.CancelledError, Exception):
                    pass

            # Send final response (split if too long)
            reply = (response.text or "").strip()
            if not reply:
                reply = "Analiz tamamlandı ancak sonuç üretilemedi. Lütfen soruyu tekrar deneyin."
            max_len = self.config.telegram.max_message_length
            if len(reply) <= max_len:
                await update.message.reply_text(reply)
            else:
                for i in range(0, len(reply), max_len):
                    chunk = reply[i:i + max_len].strip()
                    if chunk:
                        await update.message.reply_text(chunk)

        except asyncio.TimeoutError:
            log.warning(f"Telegram brain timeout (300s) for user {user_id}")
            from seriai.monitoring.telemetry import report
            report("telegram.bot", "Brain timeout (300s)", context=f"user={user_id}", severity="WARNING")
            await update.message.reply_text("İstek zaman aşımına uğradı. Tekrar deneyin.")
        except Exception as e:
            log.error(f"Telegram message handling failed: {e}")
            from seriai.monitoring.telemetry import report
            report("telegram.bot", e, context=f"user={user_id}")
            await update.message.reply_text("Bir hata oluştu. Lütfen tekrar deneyin.")

    async def _handle_error(self, update, context):
        """Handle errors."""
        log.error(f"Telegram error: {context.error}")
        from seriai.monitoring.telemetry import report
        report("telegram.framework", context.error, severity="WARNING")
