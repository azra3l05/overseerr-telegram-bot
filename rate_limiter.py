# rate_limiter.py
"""
Rate limiting for Telegram bot commands.
Prevents users from spamming commands.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from functools import wraps

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple in-memory rate limiter with sliding window."""

    def __init__(self):
        # {user_id: {command: [timestamp1, timestamp2, ...]}}
        self._buckets: Dict[int, Dict[str, List[datetime]]] = {}

    def is_allowed(
        self, user_id: int, command: str, max_calls: int, window_seconds: int
    ) -> Tuple[bool, str]:
        """
        Check if user is allowed to execute command.

        Args:
            user_id: Telegram user ID
            command: Command name (e.g., 'search', 'request', 'browse')
            max_calls: Maximum calls allowed in window
            window_seconds: Time window in seconds

        Returns:
            (is_allowed: bool, message: str)
        """
        now = datetime.now()
        cutoff = now - timedelta(seconds=window_seconds)

        # Initialize user bucket
        if user_id not in self._buckets:
            self._buckets[user_id] = {}

        if command not in self._buckets[user_id]:
            self._buckets[user_id][command] = []

        # Remove old timestamps (outside window)
        self._buckets[user_id][command] = [
            ts for ts in self._buckets[user_id][command] if ts > cutoff
        ]

        # Check if limit exceeded
        current_count = len(self._buckets[user_id][command])

        if current_count >= max_calls:
            # Calculate wait time until oldest call expires
            oldest_call = self._buckets[user_id][command][0]
            wait_until = oldest_call + timedelta(seconds=window_seconds)
            wait_seconds = int((wait_until - now).total_seconds())

            message = (
                f"⏳ **Rate Limit Exceeded**\n\n"
                f"You've used {current_count}/{max_calls} `{command}` commands in the last {window_seconds}s.\n"
                f"Please wait **{wait_seconds} seconds** before trying again."
            )
            logger.info(
                f"Rate limit hit: user={user_id}, command={command}, "
                f"count={current_count}/{max_calls}"
            )
            return False, message

        # Record this call
        self._buckets[user_id][command].append(now)
        logger.debug(
            f"Rate limit OK: user={user_id}, command={command}, "
            f"count={current_count + 1}/{max_calls}"
        )
        return True, ""

    def get_usage(self, user_id: int, command: str, window_seconds: int) -> Dict:
        """
        Get current usage statistics for a user/command.

        Returns:
            {"current": 5, "limit": 10, "window_seconds": 60, "resets_in": 23}
        """
        if user_id not in self._buckets or command not in self._buckets[user_id]:
            return {
                "current": 0,
                "limit": None,
                "window_seconds": window_seconds,
                "resets_in": 0,
            }

        now = datetime.now()
        cutoff = now - timedelta(seconds=window_seconds)

        # Clean old entries
        self._buckets[user_id][command] = [
            ts for ts in self._buckets[user_id][command] if ts > cutoff
        ]

        current_count = len(self._buckets[user_id][command])

        if current_count > 0:
            oldest_call = self._buckets[user_id][command][0]
            wait_until = oldest_call + timedelta(seconds=window_seconds)
            resets_in = int((wait_until - now).total_seconds())
        else:
            resets_in = 0

        return {
            "current": current_count,
            "window_seconds": window_seconds,
            "resets_in": resets_in,
        }

    def cleanup_old_entries(self):
        """Remove stale entries (call periodically to free memory)."""
        cutoff = datetime.now() - timedelta(hours=1)
        for user_id in list(self._buckets.keys()):
            for command in list(self._buckets[user_id].keys()):
                self._buckets[user_id][command] = [
                    ts for ts in self._buckets[user_id][command] if ts > cutoff
                ]
                if not self._buckets[user_id][command]:
                    del self._buckets[user_id][command]
            if not self._buckets[user_id]:
                del self._buckets[user_id]

        logger.debug(f"Rate limiter cleanup: {len(self._buckets)} users tracked")

    def clear_user(self, user_id: int):
        """Clear rate limits for a specific user (admin override)."""
        if user_id in self._buckets:
            del self._buckets[user_id]
            logger.info(f"Cleared rate limits for user {user_id}")

    def get_stats(self) -> Dict:
        """Get global rate limiter statistics."""
        total_users = len(self._buckets)
        total_calls = sum(
            len(commands) for user in self._buckets.values() for commands in user.values()
        )
        return {"total_users_tracked": total_users, "total_calls_tracked": total_calls}


# Global instance
_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    return _rate_limiter


def rate_limit(command: str, max_calls: int, window_seconds: int = 60):
    """
    Decorator to rate limit handler functions.

    Args:
        command: Command name for tracking
        max_calls: Maximum calls allowed in window
        window_seconds: Time window in seconds (default 60)

    Example:
        @rate_limit('search', max_calls=10, window_seconds=60)
        async def search_command(update, context):
            # ... handler code
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            user_id = update.effective_user.id

            # Check rate limit
            allowed, message = get_rate_limiter().is_allowed(
                user_id, command, max_calls, window_seconds
            )

            if not allowed:
                # Rate limit exceeded - send error message
                if update.callback_query:
                    await update.callback_query.answer(
                        f"Rate limit exceeded. Wait a moment.", show_alert=True
                    )
                    await update.callback_query.edit_message_text(
                        message, parse_mode="Markdown"
                    )
                elif update.message:
                    await update.message.reply_text(message, parse_mode="Markdown")
                return

            # Rate limit OK - execute handler
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator
