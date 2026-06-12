# config.py
"""
Configuration module with environment variable support and validation.
All sensitive data should be stored in environment variables.
"""
import os
import sys
import logging
from typing import Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment variables from {env_path}")
except ImportError:
    logger.warning("python-dotenv not installed, relying on system environment variables")


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


def _get_env(key: str, required: bool = True, default: str = None) -> str:
    """Get environment variable with validation."""
    value = os.getenv(key, default)
    if required and not value:
        raise ConfigurationError(f"Required environment variable '{key}' is not set")
    return value


def _parse_libraries(env_var: str) -> Dict[str, int]:
    """
    Parse library configuration from environment variable.
    Format: "Name1:ID1,Name2:ID2,Name3:ID3"
    Example: "Movies:1,Anime Movies:2,TV Shows:3"
    """
    libraries = {}
    raw = os.getenv(env_var, "")
    if not raw:
        return libraries
    
    try:
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            name, lib_id = item.rsplit(":", 1)
            libraries[name.strip()] = int(lib_id.strip())
    except (ValueError, AttributeError) as e:
        raise ConfigurationError(f"Invalid format for {env_var}: {e}")
    
    return libraries


def validate_config():
    """Validate all required configuration at startup."""
    errors = []
    
    # Required variables
    required_vars = [
        "TELEGRAM_BOT_TOKEN",
        "OVERSEERR_API_URL",
        "OVERSEERR_API_KEY",
        "TELEGRAMBOT_USERNAME",
        "TELEGRAMBOT_PASSWORD",
    ]
    
    for var in required_vars:
        if not os.getenv(var):
            errors.append(f"Missing required environment variable: {var}")
    
    # Validate URL format
    api_url = os.getenv("OVERSEERR_API_URL", "")
    if api_url and not (api_url.startswith("http://") or api_url.startswith("https://")):
        errors.append("OVERSEERR_API_URL must start with http:// or https://")
    
    # Validate bot token format (basic check)
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if bot_token and ":" not in bot_token:
        errors.append("TELEGRAM_BOT_TOKEN appears to be invalid (should contain ':')")
    
    # Check if libraries are configured
    if not os.getenv("LIBRARIES_MOVIES") and not os.getenv("LIBRARIES_TV"):
        logger.warning("No libraries configured (LIBRARIES_MOVIES and LIBRARIES_TV are empty)")
    
    if errors:
        error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {err}" for err in errors)
        raise ConfigurationError(error_msg)
    
    logger.info("✅ Configuration validation passed")


# ============================================================================
# Configuration Variables (loaded from environment)
# ============================================================================

# Telegram Bot
TELEGRAM_BOT_TOKEN = _get_env("TELEGRAM_BOT_TOKEN")

# TMDB API (optional, but recommended for fallback searches)
TMDB_API_KEY = _get_env("TMDB_API_KEY", required=False)

# Overseerr settings
OVERSEERR_API_URL = _get_env("OVERSEERR_API_URL")
OVERSEERR_API_KEY = _get_env("OVERSEERR_API_KEY")

# Telegrambot user credentials (must be a valid Overseerr user)
TELEGRAMBOT_USERNAME = _get_env("TELEGRAMBOT_USERNAME")
TELEGRAMBOT_PASSWORD = _get_env("TELEGRAMBOT_PASSWORD")

# Libraries configuration
# Format: "Name1:ID1,Name2:ID2,Name3:ID3"
LIBRARIES_MOVIES = _parse_libraries("LIBRARIES_MOVIES")
LIBRARIES_TV = _parse_libraries("LIBRARIES_TV")

# Logging
LOG_DIR = _get_env("LOG_DIR", required=False, default="logs")
LOG_LEVEL = _get_env("LOG_LEVEL", required=False, default="INFO")

# Data files
DATA_DIR = _get_env("DATA_DIR", required=False, default="data")
REQUESTS_LOG_FILE = os.path.join(DATA_DIR, "requests_log.json")
AVAILABILITY_WATCH_FILE = os.path.join(DATA_DIR, "availability_watch.json")

# Availability check interval (in minutes)
AVAILABILITY_CHECK_INTERVAL = int(_get_env("AVAILABILITY_CHECK_INTERVAL", required=False, default="15"))

# Message auto-delete timeout (in seconds)
MESSAGE_DELETE_TIMEOUT = int(_get_env("MESSAGE_DELETE_TIMEOUT", required=False, default="3"))
CONFIRMATION_DELETE_TIMEOUT = int(_get_env("CONFIRMATION_DELETE_TIMEOUT", required=False, default="6"))

# Postgres database configuration (for direct Radarr/Sonarr availability checking)
POSTGRES_ENABLED = _get_env("POSTGRES_ENABLED", required=False, default="true").lower() in ("true", "1", "yes")
POSTGRES_HOST = _get_env("POSTGRES_HOST", required=False, default="localhost")
POSTGRES_PORT = int(_get_env("POSTGRES_PORT", required=False, default="5432"))
POSTGRES_DATABASE = _get_env("POSTGRES_DATABASE", required=False, default="overseerr")
POSTGRES_USER = _get_env("POSTGRES_USER", required=False, default="postgres")
POSTGRES_PASSWORD = _get_env("POSTGRES_PASSWORD", required=False, default="")
POSTGRES_SCHEMA = _get_env("POSTGRES_SCHEMA", required=False, default="public")

# Radarr/Sonarr API Configuration (for real-time availability checking)
RADARR_API_URL = _get_env("RADARR_API_URL", required=False)
RADARR_API_KEY = _get_env("RADARR_API_KEY", required=False)
SONARR_API_URL = _get_env("SONARR_API_URL", required=False)
SONARR_API_KEY = _get_env("SONARR_API_KEY", required=False)

# Discord Webhook Configuration (for integration health alerts)
DISCORD_ALERTS_WEBHOOK = _get_env("DISCORD_ALERTS_WEBHOOK", required=False)

# Discord Bot Configuration (for Discord Overseerr bot)
DISCORD_BOT_TOKEN = _get_env("DISCORD_BOT_TOKEN", required=False)
DISCORD_GUILD_ID = int(_get_env("DISCORD_GUILD_ID", required=False, default="0")) if _get_env("DISCORD_GUILD_ID", required=False) else None

# Allowed Chat IDs (for group notifications)
ALLOWED_CHAT_IDS = _get_env("ALLOWED_CHAT_IDS", required=False, default="")

# Health Check Configuration
HEALTH_CHECK_PORT = int(_get_env("HEALTH_CHECK_PORT", required=False, default="9090"))

# Admin Configuration
ADMIN_USER_IDS = [int(uid.strip()) for uid in _get_env("ADMIN_USER_IDS", required=False, default="").split(",") if uid.strip()]

# Data Cleanup Configuration
WATCHLIST_FAILED_RETENTION_DAYS = int(_get_env("WATCHLIST_FAILED_RETENTION_DAYS", required=False, default="30"))
WATCHLIST_CHECKING_RETENTION_DAYS = int(_get_env("WATCHLIST_CHECKING_RETENTION_DAYS", required=False, default="120"))
REQUEST_LOG_RETENTION_MONTHS = int(_get_env("REQUEST_LOG_RETENTION_MONTHS", required=False, default="12"))


# Validate configuration on import
try:
    validate_config()
except ConfigurationError as e:
    logger.error(str(e))
    sys.exit(1)
