"""
Central configuration for MAZLUM.
All settings are env-based. No hardcoded paths, no hardcoded model names.
"""
import os
import sys
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── Base paths ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
MEMORY_DIR = DATA_DIR / "memory"
KNOWLEDGE_DIR = BASE_DIR / "seriai" / "knowledge"

# ── Platform detection ──────────────────────────────────────────────
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


@dataclass
class ModelConfig:
    """Model routing configuration."""
    # Ana beyin (cognition) — Sonnet 4
    # CRM, DB, tool_use, Telegram, routing, iş mantığı, analiz
    cognition_provider: str = "anthropic"
    cognition_model: str = "claude-sonnet-4-20250514"
    cognition_max_tokens: int = 4096

    # Hafif işler — Haiku 4.5
    # Selamlaşma, çok basit kısa sorular, özetleme, düşük riskli sınıflandırma
    light_provider: str = "anthropic"
    light_model: str = "claude-haiku-4-5-20251001"
    light_max_tokens: int = 2048

    # Ses katmanı — Gemini (STT, TTS, canlı konuşma)
    voice_provider: str = "google"
    voice_model: str = "gemini-2.5-flash"
    voice_max_tokens: int = 2048

    # Cost controls
    max_daily_cost_usd: float = 10.0
    warn_at_cost_usd: float = 7.0


@dataclass
class TelegramConfig:
    """Telegram Bot API configuration."""
    bot_token: str = ""
    allowed_user_ids: list = field(default_factory=list)
    webhook_url: str = ""
    use_polling: bool = True
    max_message_length: int = 4096


@dataclass
class DatabaseConfig:
    """Read-only database configuration."""
    engine: str = ""          # postgresql, mysql, sqlite, etc.
    host: str = ""
    port: int = 0
    name: str = ""
    user: str = ""
    password: str = ""
    readonly: bool = True     # ENFORCED - never set to False
    max_query_rows: int = 500
    query_timeout_sec: int = 30


@dataclass
class MonitoringConfig:
    """Proactive monitoring configuration."""
    check_interval_sec: int = 300        # Her kaç saniyede kontrol
    dead_letter_threshold: int = 10      # Callback hata eşiği
    pending_tx_threshold: int = 100      # Bekleyen işlem eşiği
    voice_notify_cooldown_sec: int = 300  # Sesli bildirimler arası min süre
    enable_proactive: bool = True        # Proaktif izleme açık mı


@dataclass
class AppConfig:
    """Main application configuration."""
    name: str = "MAZLUM"
    owner_name: str = "Efendim"
    language: str = "tr"
    web_port: int = 8420
    debug: bool = False
    log_level: str = "INFO"

    models: ModelConfig = field(default_factory=ModelConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    # Feature flags
    enable_voice: bool = False
    enable_telegram: bool = False
    enable_web_ui: bool = True
    enable_db_intelligence: bool = False
    enable_legal_pack: bool = False  # optional, off by default

    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)


def load_config() -> AppConfig:
    """Load configuration from environment variables."""
    cfg = AppConfig()

    # Models
    cfg.models.cognition_model = os.getenv("SERIAI_COGNITION_MODEL", cfg.models.cognition_model)
    cfg.models.light_model = os.getenv("SERIAI_LIGHT_MODEL", cfg.models.light_model)
    cfg.models.voice_model = os.getenv("SERIAI_VOICE_MODEL", cfg.models.voice_model)
    try:
        cfg.models.max_daily_cost_usd = float(os.getenv("SERIAI_MAX_DAILY_COST", str(cfg.models.max_daily_cost_usd)))
    except (ValueError, TypeError):
        pass  # Keep default

    # API keys
    # These are read at provider init time, not stored in config object

    # Telegram
    cfg.telegram.bot_token = os.getenv("SERIAI_TELEGRAM_BOT_TOKEN", "")
    allowed = os.getenv("SERIAI_TELEGRAM_ALLOWED_USERS", "")
    if allowed:
        parsed_ids = []
        for x in allowed.split(","):
            x = x.strip()
            if x:
                try:
                    parsed_ids.append(int(x))
                except ValueError:
                    warnings.warn(f"Geçersiz Telegram User ID atlandı: {x}")
        cfg.telegram.allowed_user_ids = parsed_ids
    cfg.enable_telegram = bool(cfg.telegram.bot_token)

    # Database
    cfg.database.engine = os.getenv("SERIAI_DB_ENGINE", "")
    cfg.database.host = os.getenv("SERIAI_DB_HOST", "")
    try:
        cfg.database.port = int(os.getenv("SERIAI_DB_PORT", "0") or "0")
    except (ValueError, TypeError):
        cfg.database.port = 0
    cfg.database.name = os.getenv("SERIAI_DB_NAME", "")
    cfg.database.user = os.getenv("SERIAI_DB_USER", "")
    cfg.database.password = os.getenv("SERIAI_DB_PASSWORD", "")
    cfg.database.readonly = True  # ALWAYS
    cfg.enable_db_intelligence = bool(cfg.database.engine and cfg.database.host)

    # Features
    cfg.enable_voice = os.getenv("SERIAI_ENABLE_VOICE", "false").lower() == "true"
    cfg.enable_web_ui = os.getenv("SERIAI_ENABLE_WEB_UI", "true").lower() == "true"
    cfg.enable_legal_pack = os.getenv("SERIAI_ENABLE_LEGAL_PACK", "false").lower() == "true"

    # App
    cfg.owner_name = os.getenv("SERIAI_OWNER_NAME", cfg.owner_name)
    cfg.debug = os.getenv("SERIAI_DEBUG", "false").lower() == "true"
    cfg.log_level = os.getenv("SERIAI_LOG_LEVEL", "INFO")
    try:
        cfg.web_port = int(os.getenv("SERIAI_WEB_PORT", str(cfg.web_port)))
    except (ValueError, TypeError):
        pass  # Keep default
    cfg.language = os.getenv("SERIAI_LANGUAGE", "tr")

    # Monitoring
    try:
        cfg.monitoring.check_interval_sec = int(os.getenv("SERIAI_MONITOR_INTERVAL", str(cfg.monitoring.check_interval_sec)))
    except (ValueError, TypeError):
        pass
    try:
        cfg.monitoring.dead_letter_threshold = int(os.getenv("SERIAI_DEAD_LETTER_THRESHOLD", str(cfg.monitoring.dead_letter_threshold)))
    except (ValueError, TypeError):
        pass
    try:
        cfg.monitoring.pending_tx_threshold = int(os.getenv("SERIAI_PENDING_TX_THRESHOLD", str(cfg.monitoring.pending_tx_threshold)))
    except (ValueError, TypeError):
        pass
    try:
        cfg.monitoring.voice_notify_cooldown_sec = int(os.getenv("SERIAI_VOICE_NOTIFY_COOLDOWN", str(cfg.monitoring.voice_notify_cooldown_sec)))
    except (ValueError, TypeError):
        pass
    cfg.monitoring.enable_proactive = os.getenv("SERIAI_ENABLE_PROACTIVE", "true").lower() == "true"

    return cfg
