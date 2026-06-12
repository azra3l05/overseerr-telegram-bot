# handlers/__init__.py
"""
Telegram bot handlers package.
Exports all command handlers, callback handlers, and utility functions.
"""

# Import command handlers
from handlers.commands import (
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
)

# Import callback handlers
from handlers.callbacks import (
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
    ask_library,
)

# Import utility functions
from handlers.utils import (
    track_message,
    cleanup_messages,
    schedule_autodelete,
    send_rich_poster,
)

__all__ = [
    # Command handlers
    "start",
    "stats_command",
    "search_movie",
    "search_tv",
    "my_requests",
    "pending_requests",
    "request_status",
    "browse_popular",
    "backup_database_command",
    "restore_database_command",
    "cache_stats_command",
    "cache_clear_command",
    "set_priority_command",
    "priority_queue_command",
    "notification_history_command",
    "delete_request_handler",
    "cleanup_command",
    # Callback handlers
    "button_handler",
    "library_handler",
    "season_handler",
    "confirm_handler",
    "cancel_handler",
    "dismiss_handler",
    "inline_search",
    "inlineopen_handler",
    "recommend_handler",
    "openrec_handler",
    "asklib_wrapper",
    "ask_library",
    # Utility functions
    "track_message",
    "cleanup_messages",
    "schedule_autodelete",
    "send_rich_poster",
]
