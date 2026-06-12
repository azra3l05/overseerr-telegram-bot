#!/usr/bin/env python3
"""
Telegram Overseerr Bot - Main Entry Point
Refactored modular version with separated concerns:
- handlers.py: All Telegram command and callback handlers
- availability.py: Background availability checking job
- config.py: Configuration management
- database.py: Data persistence
- overseerr_api.py: Overseerr API integration
"""

import logging
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

# Import configuration
from config import (
    TELEGRAM_BOT_TOKEN,
    POSTGRES_ENABLED,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DATABASE,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_SCHEMA,
    RADARR_API_URL,
    RADARR_API_KEY,
    SONARR_API_URL,
    SONARR_API_KEY,
    HEALTH_CHECK_PORT,
)

# Import handlers
from handlers import (
    start,
    stats_command,
    search_movie,
    search_tv,
    my_requests,
    pending_requests,
    request_status,
    browse_popular,
    backup_database_command,
    restore_database_command,
    cache_stats_command,
    cache_clear_command,
    set_priority_command,
    priority_queue_command,
    notification_history_command,
    delete_request_handler,
    cleanup_command,
    button_handler,
    library_handler,
    season_handler,
    confirm_handler,
    cancel_handler,
    dismiss_handler,
    inline_search,
    inlineopen_handler,
    recommend_handler,
    openrec_handler,
    asklib_wrapper,
)

# Import availability checker
from availability import check_availability_job, checknow_command, weekly_pending_digest

# Import health check
from health_check import start_health_check_server, get_health_status

# Import integration monitoring
from integration_monitor import health_check_job, health_status_command, set_discord_webhook

# Import backup
from backup import scheduled_backup

# Import data cleanup
from data_cleanup import scheduled_cleanup, cleanup_all

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    """Start the bot."""
    logger.info("Starting Telegram Overseerr Bot...")

    # Start health check server
    start_health_check_server(port=HEALTH_CHECK_PORT)

    # Configure Discord webhook for integration monitoring
    from config import DISCORD_ALERTS_WEBHOOK
    if DISCORD_ALERTS_WEBHOOK:
        set_discord_webhook(DISCORD_ALERTS_WEBHOOK)
        logger.info("✅ Discord webhook configured for integration alerts")
    else:
        logger.warning("Discord webhook not configured, integration alerts disabled")

    # Initialize Postgres checker if enabled
    if POSTGRES_ENABLED:
        try:
            from postgres_checker import init_postgres_checker
            init_postgres_checker(
                POSTGRES_HOST,
                POSTGRES_PORT,
                POSTGRES_DATABASE,
                POSTGRES_USER,
                POSTGRES_PASSWORD,
                POSTGRES_SCHEMA
            )
            logger.info("✅ PostgresChecker initialized successfully")
        except Exception as e:
            logger.warning(f"Could not initialize PostgresChecker: {e}")
    
    # Initialize Radarr/Sonarr API if configured
    if RADARR_API_URL and RADARR_API_KEY and SONARR_API_URL and SONARR_API_KEY:
        try:
            from radarr_sonarr_api import init_radarr_sonarr_api
            init_radarr_sonarr_api(
                RADARR_API_URL,
                RADARR_API_KEY,
                SONARR_API_URL,
                SONARR_API_KEY
            )
            logger.info("✅ RadarrSonarrAPI initialized successfully")
        except Exception as e:
            logger.warning(f"Could not initialize RadarrSonarrAPI: {e}")

    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("searchmovie", search_movie))
    application.add_handler(CommandHandler("searchtv", search_tv))
    application.add_handler(CommandHandler("myrequests", my_requests))
    application.add_handler(CommandHandler("pending", pending_requests))
    application.add_handler(CommandHandler("status", request_status))
    application.add_handler(CommandHandler("browse", browse_popular))
    application.add_handler(CommandHandler("backup", backup_database_command))
    application.add_handler(CommandHandler("restore", restore_database_command))
    application.add_handler(CommandHandler("cachestats", cache_stats_command))
    application.add_handler(CommandHandler("cacheclear", cache_clear_command))
    application.add_handler(CommandHandler("setpriority", set_priority_command))
    application.add_handler(CommandHandler("priorityqueue", priority_queue_command))
    application.add_handler(CommandHandler("notifications", notification_history_command))
    application.add_handler(CommandHandler("checknow", checknow_command))
    application.add_handler(CommandHandler("health", health_status_command))
    application.add_handler(CommandHandler("cleanup", cleanup_command))

    # Register callback query handlers
    application.add_handler(CallbackQueryHandler(button_handler, pattern=r"^(movie|tv):\d+$"))
    application.add_handler(CallbackQueryHandler(asklib_wrapper, pattern=r"^asklib:"))
    application.add_handler(CallbackQueryHandler(library_handler, pattern=r"^lib:"))
    application.add_handler(CallbackQueryHandler(season_handler, pattern=r"^season:"))
    application.add_handler(CallbackQueryHandler(confirm_handler, pattern=r"^confirm:"))
    application.add_handler(CallbackQueryHandler(cancel_handler, pattern=r"^cancel$"))
    application.add_handler(CallbackQueryHandler(dismiss_handler, pattern=r"^dismiss$"))
    application.add_handler(CallbackQueryHandler(recommend_handler, pattern=r"^recommend:"))
    application.add_handler(CallbackQueryHandler(openrec_handler, pattern=r"^openrec:"))
    application.add_handler(CallbackQueryHandler(delete_request_handler, pattern=r"^delreq:"))

    # Register inline query handler
    application.add_handler(InlineQueryHandler(inline_search))

    # Register message handler for inline selections
    application.add_handler(MessageHandler(filters.Regex(r"^/_inlineopen"), inlineopen_handler))

    # Schedule availability checking job (every 15 minutes)
    job_queue = application.job_queue
    job_queue.run_repeating(check_availability_job, interval=900, first=60)

    # Schedule daily backup job (runs at 3 AM)
    from datetime import time
    job_queue.run_daily(scheduled_backup, time=time(hour=3, minute=0))
    logger.info("✅ Scheduled daily backup at 3:00 AM")

    # Schedule daily data cleanup (runs at 4 AM, after backup)
    job_queue.run_daily(scheduled_cleanup, time=time(hour=4, minute=0))
    logger.info("✅ Scheduled daily data cleanup at 4:00 AM")

    # Schedule weekly pending digest (Sundays at 10 AM)
    job_queue.run_daily(weekly_pending_digest, time=time(hour=10, minute=0), days=(6,))  # 6 = Sunday
    logger.info("✅ Scheduled weekly pending digest on Sundays at 10:00 AM")
    # Schedule daily quality upgrade check (runs at 3:30 AM)
    from availability import daily_quality_check_job
    job_queue.run_daily(daily_quality_check_job, time=time(hour=3, minute=30))
    logger.info("✅ Scheduled daily quality upgrade check at 3:30 AM")

    # Schedule cache cleanup (every hour)
    from cache import get_cache
    def cache_cleanup_job(context):
        get_cache().cleanup_expired()
    job_queue.run_repeating(cache_cleanup_job, interval=3600, first=3600)
    logger.info("✅ Scheduled cache cleanup every hour")

    # Schedule integration health monitoring (every 5 minutes)
    job_queue.run_repeating(health_check_job, interval=300, first=60)
    logger.info("✅ Scheduled integration health check every 5 minutes")

    logger.info("Bot started successfully. Polling for updates...")

    # Start the bot
    application.run_polling(allowed_updates=["message", "callback_query", "inline_query"])


if __name__ == "__main__":
    main()
