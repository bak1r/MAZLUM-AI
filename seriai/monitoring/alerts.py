"""
Callback failure alerting system.
Periodically checks for dead letter callbacks and notifies operators via Telegram.

Dead letter = payment_transactions where status=1 (onaylanmış) but callback_status = false (başarılı).
These are critical: customer's money was processed but site wasn't notified.
"""
import logging
import asyncio
from typing import Optional
from datetime import datetime

log = logging.getLogger("seriai.monitoring.alerts")

# Defaults
DEFAULT_CHECK_INTERVAL_SEC = 300  # 5 minutes
DEFAULT_DEAD_LETTER_THRESHOLD = 10  # alert if more than this many


class CallbackAlertMonitor:
    """
    Monitors for dead letter callbacks and sends alerts via Telegram.
    Runs as an asyncio background task alongside other services.
    """

    def __init__(self, db, telegram_bot_token: str, alert_chat_ids: list[int],
                 check_interval: int = DEFAULT_CHECK_INTERVAL_SEC,
                 threshold: int = DEFAULT_DEAD_LETTER_THRESHOLD):
        self.db = db
        self.bot_token = telegram_bot_token
        self.alert_chat_ids = alert_chat_ids
        self.check_interval = check_interval
        self.threshold = threshold
        self._running = False
        self._last_count = 0
        self._last_alert_time: Optional[datetime] = None
        self._min_alert_interval = 600  # Don't spam: minimum 10 min between alerts

    async def start(self):
        """Start the monitoring loop."""
        if not self.bot_token or not self.alert_chat_ids:
            log.warning("Alert monitor: Telegram token or chat IDs not configured. Skipping.")
            return

        self._running = True
        log.info(f"Callback alert monitor started. Interval: {self.check_interval}s, Threshold: {self.threshold}")

        _consecutive_failures = 0
        try:
            while self._running:
                try:
                    await self._check_and_alert()
                    _consecutive_failures = 0
                except Exception as e:
                    _consecutive_failures += 1
                    log.error(f"Alert check failed ({_consecutive_failures}x): {e}")
                    if _consecutive_failures >= 5:
                        log.error("Alert monitor: 5 consecutive failures, backing off to 5min")
                # Backoff on repeated failures
                sleep_time = min(self.check_interval * (2 ** min(_consecutive_failures, 3)), 300)
                await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            self._running = False
            log.info("Callback alert monitor stopped.")

    async def _check_and_alert(self):
        """Check dead letter count and send alert if above threshold."""
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.db.query(
                    "SELECT COUNT(*) as cnt FROM payment_transactions "
                    "WHERE status = 1 AND callback_status = false "
                    "AND created_at >= NOW() - INTERVAL '24 hours'"
                )
            )

            if "error" in result:
                log.error(f"Alert query failed: {result['error']}")
                return

            count = result.get("rows", [{}])[0].get("cnt", 0)
            log.debug(f"Dead letter callbacks (24h): {count}")

            if count >= self.threshold:
                # Check if we should alert (don't spam)
                now = datetime.now()
                should_alert = (
                    self._last_alert_time is None
                    or (now - self._last_alert_time).total_seconds() >= self._min_alert_interval
                    or count > self._last_count * 1.5  # Alert if count jumped >50%
                )

                if should_alert:
                    await self._send_alert(count)
                    self._last_alert_time = now

            self._last_count = count

        except Exception as e:
            log.error(f"Alert check failed: {e}")

    async def _send_alert(self, count: int):
        """Send alert via Telegram Bot API (direct HTTP, no dependency on bot instance)."""
        # Get breakdown for context
        loop = asyncio.get_running_loop()
        breakdown = await loop.run_in_executor(None, self._get_breakdown)

        message = (
            f"⚠️ CALLBACK ALERT\n\n"
            f"Son 24 saatte {count} adet callback başarısız işlem tespit edildi.\n\n"
        )

        if breakdown:
            message += "Site bazlı dağılım:\n"
            for row in breakdown[:10]:  # Top 10 sites
                message += f"  • {row.get('site_name', 'Bilinmeyen')}: {row.get('cnt', 0)} işlem\n"
            message += "\n"

        message += (
            f"Eşik değeri: {self.threshold}\n"
            f"Kontrol aralığı: {self.check_interval // 60} dk\n\n"
            f"serialhavale.com panelinden kontrol ediniz."
        )

        import urllib.request
        import json

        loop = asyncio.get_running_loop()
        for chat_id in self.alert_chat_ids:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                data = json.dumps({
                    "chat_id": chat_id,
                    "text": message,
                }).encode("utf-8")

                def _do_send(url=url, data=data):
                    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        resp.read()

                await loop.run_in_executor(None, _do_send)
                log.info(f"Alert sent to chat_id={chat_id}, dead_letters={count}")
            except Exception as e:
                log.error(f"Failed to send alert to {chat_id}: {e}")

    def _get_breakdown(self) -> list:
        """Get per-site breakdown of dead letter callbacks."""
        try:
            result = self.db.query(
                "SELECT s.name as site_name, COUNT(*) as cnt "
                "FROM payment_transactions pt "
                "JOIN sites s ON s.id = pt.site_id "
                "WHERE pt.status = 1 AND pt.callback_status = false "
                "AND pt.created_at >= NOW() - INTERVAL '24 hours' "
                "GROUP BY s.name ORDER BY cnt DESC LIMIT 10"
            )
            if "error" not in result:
                return result.get("rows", [])
        except Exception:
            pass
        return []

    def stop(self):
        """Stop the monitor."""
        self._running = False
