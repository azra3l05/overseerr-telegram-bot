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
    delete_request_handler,
    button_handler,
    library_handler,
    season_handler,
    confirm_handler,
    cancel_handler,
    inline_search,
    inlineopen_handler,
    recommend_handler,
    openrec_handler,
    asklib_wrapper,
)

# Import availability checker
from availability import check_availability_job, checknow_command

# Import health check
from health_check import start_health_check_server, get_health_status

# Import backup
from backup import scheduled_backup

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
    application.add_handler(CommandHandler("checknow", checknow_command))

    # Register callback query handlers
    application.add_handler(CallbackQueryHandler(button_handler, pattern=r"^(movie|tv):\d+$"))
    application.add_handler(CallbackQueryHandler(asklib_wrapper, pattern=r"^asklib:"))
    application.add_handler(CallbackQueryHandler(library_handler, pattern=r"^lib:"))
    application.add_handler(CallbackQueryHandler(season_handler, pattern=r"^season:"))
    application.add_handler(CallbackQueryHandler(confirm_handler, pattern=r"^confirm:"))
    application.add_handler(CallbackQueryHandler(cancel_handler, pattern=r"^cancel$"))
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

    logger.info("Bot started successfully. Polling for updates...")

    # Start the bot
    application.run_polling(allowed_updates=["message", "callback_query", "inline_query"])


if __name__ == "__main__":
    main()
