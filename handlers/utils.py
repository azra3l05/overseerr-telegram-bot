# handlers/utils.py
"""
Shared utilities for handlers.
Message tracking, poster sending, and common functions.
"""
import logging
from typing import Any, Dict, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from overseerr_api import get_details
from config import LIBRARIES_MOVIES, LIBRARIES_TV
from utils import (
    safe_year, title_with_year_from_details, is_available,
    imdb_url_from_details
)

logger = logging.getLogger(__name__)

# Aliases for backward compatibility
MOVIE_LIBRARIES = LIBRARIES_MOVIES
TV_LIBRARIES = LIBRARIES_TV

# State & Message Tracking
user_context: Dict[int, Dict[str, Any]] = {}  # ephemeral per-user flow state


def _cmd_log(cmd_name: str, update: Update, **params) -> None:
    """Structured audit-log entry for a Telegram command invocation.

    Tag prefix [CMD] makes these greppable in the bot's stderr/stdout log file.
    Caller passes params as kwargs; None values are omitted to keep lines tight.
    """
    user = update.effective_user
    user_label = f"@{user.username}" if user.username else user.full_name
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    if params:
        params_str = ", ".join(f"{k}={v!r}" for k, v in params.items() if v is not None)
        logger.info(f"[CMD] /{cmd_name} by {user_label} ({user.id}) in chat {chat_id}: {params_str}")
    else:
        logger.info(f"[CMD] /{cmd_name} by {user_label} ({user.id}) in chat {chat_id}")


def _event_log(event: str, update: Optional[Update] = None, **fields) -> None:
    """Structured log for a non-command bot event (selection, denial, submission).

    Tag prefix [EVT] makes flow events greppable separately from commands.
    """
    user = ""
    if update is not None and update.effective_user is not None:
        u = update.effective_user
        user_label = f"@{u.username}" if u.username else u.full_name
        user = f" by {user_label} ({u.id})"
    fields_str = ", ".join(f"{k}={v!r}" for k, v in fields.items() if v is not None)
    logger.info(f"[EVT] {event}{user}: {fields_str}" if fields_str else f"[EVT] {event}{user}")


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram Markdown.

    Telegram Markdown requires escaping these characters:
    _ * [ ] ( ) ~ ` > # + - = | { } . !

    Args:
        text: The text to escape

    Returns:
        Text with Markdown special characters escaped
    """
    if not text:
        return text

    # Characters that need escaping in Telegram Markdown
    escape_chars = r'_*[]()~`>#+-=|{}.!'

    # Escape each special character with a backslash
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')

    return text


def track_message(user_id: int, message_id: int):
    """Track a message ID for later cleanup."""
    if user_id not in user_context:
        user_context[user_id] = {"messages": []}
    user_context[user_id]["messages"].append(message_id)


async def cleanup_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Clean up old messages for a user."""
    if user_id not in user_context:
        return
    messages = user_context[user_id].get("messages", [])
    for msg_id in messages:
        try:
            await context.bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
    user_context[user_id]["messages"] = []


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """Job to delete a message after delay."""
    chat_id = context.job.data["chat_id"]
    message_id = context.job.data["message_id"]
    try:
        await context.bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def schedule_autodelete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, seconds: int = 3):
    """Schedule a message for auto-deletion."""
    if not context.job_queue:
        return
    context.job_queue.run_once(
        _delete_message_job,
        seconds,
        data={"chat_id": chat_id, "message_id": message_id},
        name=f"delete_{chat_id}_{message_id}"
    )


async def send_rich_poster(
    chat_id: int,
    details: dict,
    media_type: str,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: Optional[int] = None
):
    """
    Send a rich media card with poster and details.

    Args:
        chat_id: Telegram chat ID
        details: Media details from Overseerr
        media_type: "movie" or "tv"
        context: Telegram context
        user_id: Optional user ID for message tracking
    """
    title = title_with_year_from_details(details, media_type)
    overview = details.get("overview", "No description available.")

    # Truncate long overviews
    if len(overview) > 500:
        overview = overview[:497] + "..."

    # Get poster URL
    poster_path = details.get("posterPath")
    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

    # Build message
    message_lines = [f"*{escape_markdown(title)}*", "", overview]

    # Add ratings
    vote_average = details.get("voteAverage")
    if vote_average:
        stars = "⭐" * int(vote_average / 2)
        message_lines.append(f"\n{stars} {vote_average:.1f}/10")

    # Add status
    status = details.get("mediaInfo", {}).get("status")
    if status == 5:  # Available
        message_lines.append("\n✅ *Available*")
    elif status in (2, 3):  # Pending or Processing
        message_lines.append("\n⏳ *Processing*")

    # Add IMDb link
    imdb_url = imdb_url_from_details(details)
    if imdb_url:
        message_lines.append(f"\n[View on IMDb]({imdb_url})")

    caption = "\n".join(message_lines)

    # Build request buttons
    media_id = details.get("id") or details.get("tmdbId")
    keyboard = []

    # Add request button (triggers library selection)
    if media_id:
        keyboard.append([
            InlineKeyboardButton(
                "📥 Request",
                callback_data=f"asklib:{media_type}:{media_id}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    # Send with poster if available
    try:
        if poster_url:
            msg = await context.bot.send_photo(
                chat_id,
                photo=poster_url,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            msg = await context.bot.send_message(
                chat_id,
                caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )

        if user_id:
            track_message(user_id, msg.message_id)

        return msg
    except Exception as e:
        logger.error(f"Error sending rich poster: {e}")
        # Fallback to text-only
        msg = await context.bot.send_message(
            chat_id,
            caption,
            parse_mode="Markdown"
        )
        if user_id:
            track_message(user_id, msg.message_id)
        return msg


async def asklib_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wrapper for ask_library callback."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":", 3)
    if len(parts) < 4:
        await query.edit_message_text("Invalid request format.")
        return

    media_type, media_id = parts[1], parts[2]

    try:
        media_id = int(media_id)
    except Exception:
        await query.edit_message_text("Invalid media ID.")
        return

    await ask_library(query, media_type, media_id)


async def ask_library(query, media_type: str, media_id: int):
    """
    Ask user to select a library for their request.

    Args:
        query: Callback query
        media_type: "movie" or "tv"
        media_id: TMDB ID
    """
    try:
        details = get_details(media_id, media_type)
        title = title_with_year_from_details(details, media_type)

        # Build library keyboard
        libraries = MOVIE_LIBRARIES if media_type == "movie" else TV_LIBRARIES
        keyboard = []

        for lib_name, lib_id in libraries.items():
            keyboard.append([
                InlineKeyboardButton(
                    lib_name,
                    callback_data=f"lib:{media_type}:{media_id}:{lib_id}"
                )
            ])

        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

        await query.edit_message_text(
            f"Select library for:\n\n*{escape_markdown(title)}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.exception("ask_library error: %s", e)
        await query.edit_message_text("Error loading libraries. Please try again.")
