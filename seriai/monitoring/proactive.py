"""
Proactive database monitoring with rule engine.
Periodically checks predefined rules against the DB and alerts via voice + Telegram.
No LLM call — direct SQL queries for speed (100-500ms).
"""
import logging
import asyncio
import time
from typing import Optional
from datetime import datetime

log = logging.getLogger("seriai.monitoring.proactive")


# ── Rule definitions ─────────────────────────────────────────────
# Each rule: name, sql, threshold, severity, message_template, cooldown_sec
# Severity: CRITICAL → voice + telegram, HIGH → voice + telegram, MEDIUM → telegram only

MONITORING_RULES = [
    {
        "name": "dead_letter_spike",
        "description": "Son 1 saatte callback hatası eşik üstü",
        "sql": (
            "SELECT COUNT(*) as cnt FROM payment_transactions "
            "WHERE status = 1 AND callback_status = false "
            "AND created_at >= NOW() - INTERVAL '1 hour'"
        ),
        "value_key": "cnt",
        "threshold": 10,  # overridden by config.dead_letter_threshold
        "severity": "HIGH",
        "message_template": "⚠️ Son 1 saatte {value} adet callback başarısız işlem var. Eşik: {threshold}.",
        "cooldown_sec": 600,
    },
    {
        "name": "pending_transactions",
        "description": "Son 1 saatte bekleyen işlem eşik üstü",
        "sql": (
            "SELECT COUNT(*) as cnt FROM payment_transactions "
            "WHERE status = 0 "
            "AND created_at >= NOW() - INTERVAL '1 hour'"
        ),
        "value_key": "cnt",
        "threshold": 100,  # overridden by config.pending_tx_threshold
        "severity": "MEDIUM",
        "message_template": "📊 Son 1 saatte {value} adet bekleyen işlem var. Eşik: {threshold}.",
        "cooldown_sec": 900,
    },
    {
        "name": "dead_letter_growth",
        "description": "Son 15 dakikada hızlı callback hatası artışı",
        "sql": (
            "SELECT COUNT(*) as cnt FROM payment_transactions "
            "WHERE status = 1 AND callback_status = false "
            "AND created_at >= NOW() - INTERVAL '15 minutes'"
        ),
        "value_key": "cnt",
        "threshold": 5,
        "severity": "CRITICAL",
        "message_template": "🚨 KRİTİK: Son 15 dakikada {value} yeni callback hatası! Hızlı artış tespit edildi.",
        "cooldown_sec": 300,
    },
]


class ProactiveMonitor:
    """
    Rule-based proactive DB monitor.
    Checks SQL rules periodically and alerts via voice + Telegram.
    """

    def __init__(self, db, config, voice_engine=None,
                 bot_token: str = "", alert_chat_ids: list = None):
        self.db = db
        self.config = config
        self.voice_engine = voice_engine  # VoiceEngine instance for inject_notification
        self.bot_token = bot_token
        self.alert_chat_ids = alert_chat_ids or []
        self._running = False
        self._rule_last_alert: dict[str, float] = {}  # rule_name → timestamp
        self._rule_last_value: dict[str, int] = {}     # rule_name → last value

    def set_voice_engine(self, voice_engine):
        """Set voice engine reference (may be available after init)."""
        self.voice_engine = voice_engine

    async def start(self):
        """Start the monitoring loop."""
        self._running = True
        interval = self.config.monitoring.check_interval_sec
        log.info(f"Proactive monitor started. Interval: {interval}s, Rules: {len(MONITORING_RULES)}")

        # Apply config thresholds — use local copy, don't mutate global
        import copy
        self._rules = copy.deepcopy(MONITORING_RULES)
        for rule in self._rules:
            if rule["name"] == "dead_letter_spike":
                rule["threshold"] = self.config.monitoring.dead_letter_threshold
            elif rule["name"] == "pending_transactions":
                rule["threshold"] = self.config.monitoring.pending_tx_threshold

        _consecutive_failures = 0
        try:
            # İlk kontrol 30 saniye sonra (sistem başlasın)
            await asyncio.sleep(30)

            while self._running:
                try:
                    await self._evaluate_rules()
                    _consecutive_failures = 0
                except Exception as e:
                    _consecutive_failures += 1
                    log.error(f"Proactive check failed ({_consecutive_failures}x): {e}")
                    if _consecutive_failures >= 5:
                        log.error("Proactive monitor: 5 consecutive failures, backing off")

                sleep_time = min(
                    interval * (2 ** min(_consecutive_failures, 3)),
                    600,  # max 10 min
                )
                await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            self._running = False
            log.info("Proactive monitor stopped.")

    async def _evaluate_rules(self):
        """Evaluate all monitoring rules."""
        loop = asyncio.get_running_loop()
        now = time.time()
        triggered = 0

        for rule in self._rules:
            try:
                # Run DB query in executor (non-blocking)
                result = await loop.run_in_executor(
                    None, lambda sql=rule["sql"]: self.db.query(sql)
                )

                if "error" in result:
                    log.warning(f"Rule '{rule['name']}' query failed: {result['error']}")
                    continue

                rows = result.get("rows", [])
                if not rows:
                    continue

                value = rows[0].get(rule["value_key"], 0)
                if value is None:
                    value = 0

                # Store last value
                self._rule_last_value[rule["name"]] = value

                # Check threshold
                if value < rule["threshold"]:
                    continue

                # Check cooldown
                last_alert = self._rule_last_alert.get(rule["name"], 0)
                if (now - last_alert) < rule["cooldown_sec"]:
                    continue

                # TRIGGERED!
                triggered += 1
                message = rule["message_template"].format(
                    value=value, threshold=rule["threshold"]
                )
                severity = rule["severity"]
                log.warning(f"Rule triggered: {rule['name']} ({severity}) — {message}")

                # Voice notification for HIGH/CRITICAL
                if severity in ("HIGH", "CRITICAL") and self.voice_engine:
                    voice_cooldown = self.config.monitoring.voice_notify_cooldown_sec
                    if (now - last_alert) >= voice_cooldown:
                        try:
                            await self.voice_engine.inject_notification(
                                f"[PROAKTİF UYARI — {severity}] {message}"
                            )
                        except Exception as e:
                            log.error(f"Voice notification failed: {e}")

                # Telegram notification for all severities
                if self.bot_token and self.alert_chat_ids:
                    await self._send_telegram_alert(rule, message, severity)

                self._rule_last_alert[rule["name"]] = now

            except Exception as e:
                log.error(f"Rule '{rule['name']}' evaluation error: {e}")

        if triggered:
            log.info(f"Proactive check: {triggered} rule(s) triggered")
        else:
            log.debug("Proactive check completed — no alerts")

    async def _send_telegram_alert(self, rule: dict, message: str, severity: str):
        """Send alert via Telegram Bot API (direct HTTP)."""
        import urllib.request
        import json

        severity_emoji = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "📊"}.get(severity, "ℹ️")
        full_message = (
            f"{severity_emoji} PROAKTİF İZLEME\n\n"
            f"{message}\n\n"
            f"Kural: {rule['description']}\n"
            f"Zaman: {datetime.now().strftime('%H:%M:%S')}"
        )

        loop = asyncio.get_running_loop()
        for chat_id in self.alert_chat_ids:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                data = json.dumps({
                    "chat_id": chat_id,
                    "text": full_message,
                }).encode("utf-8")

                def _do_send(url=url, data=data):
                    req = urllib.request.Request(
                        url, data=data,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        resp.read()

                await loop.run_in_executor(None, _do_send)
                log.info(f"Proactive alert sent to chat_id={chat_id}: {rule['name']}")
            except Exception as e:
                log.error(f"Failed to send proactive alert to {chat_id}: {e}")

    def get_status(self) -> dict:
        """Return current monitor status for debugging / web UI."""
        return {
            "running": self._running,
            "rules": len(MONITORING_RULES),
            "last_values": dict(self._rule_last_value),
            "last_alerts": {
                k: datetime.fromtimestamp(v).strftime("%H:%M:%S")
                for k, v in self._rule_last_alert.items()
            },
        }

    def stop(self):
        """Stop the monitor."""
        self._running = False
