# handlers/commands.py
"""
Command handlers for the Telegram bot.
Handles /start, /search, /browse, /stats, /backup, etc.
"""
import logging
from typing import List
from collections import Counter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes
import uuid

from overseerr_api import search_media, get_details, get_discover_content
from config import TMDB_API_KEY
from database import (
    get_user_requests, get_all_requests, remove_from_watchlist,
    get_user_watchlist_count, set_request_priority,
    get_pending_requests_by_priority, get_priority_emoji
)
from utils import title_with_year_from_details, is_available, tmdb_search
from rate_limiter import rate_limit
from cache import get_cache
from notifications import get_notification_manager
from .utils import (
    track_message, cleanup_messages, schedule_autodelete,
    send_rich_poster, MOVIE_LIBRARIES, TV_LIBRARIES,
    _cmd_log, _event_log, escape_markdown
)

import requests

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    _cmd_log("start", update)
    welcome_msg = (
        "👋 *Welcome to the Overseerr Request Bot!*\n\n"
        "Search and request movies and TV shows directly from Telegram.\n\n"
        "*Commands:*\n"
        "/searchmovie <title> - Search for movies\n"
        "/searchtv <title> - Search for TV shows\n"
        "/browse - Browse trending content\n"
        "/myrequests - View your requests\n"
        "/pending - View pending requests\n"
        "/stats - View bot statistics\n\n"
        "*Inline Search:*\n"
        "Type `@bot_name <query>` in any chat to search inline"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display bot statistics."""
    _cmd_log("stats", update)
    try:
        all_requests = get_all_requests()

        if not all_requests:
            await update.message.reply_text("📊 No requests logged yet.")
            return

        # Count by type
        movie_count = sum(1 for r in all_requests if r.get("type") == "movie")
        tv_count = sum(1 for r in all_requests if r.get("type") == "tv")

        # Top users
        user_counter = Counter(r.get("user", "Unknown") for r in all_requests)
        top_users = user_counter.most_common(5)

        # Top libraries
        lib_counter = Counter(r.get("library", "Unknown") for r in all_requests)
        top_libs = lib_counter.most_common(5)

        stats_lines = [
            "*📊 Bot Statistics*\n",
            f"*Total Requests:* {len(all_requests)}",
            f"  • Movies: {movie_count}",
            f"  • TV Shows: {tv_count}\n",
            "*👥 Top Users:*"
        ]

        for user, count in top_users:
            stats_lines.append(f"  • {escape_markdown(user)}: {count}")

        stats_lines.append("\n*📚 Top Libraries:*")
        for lib, count in top_libs:
            stats_lines.append(f"  • {escape_markdown(lib)}: {count}")

        await update.message.reply_text(
            "\n".join(stats_lines),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Error generating stats")
        await update.message.reply_text("❌ Error generating statistics.")


@rate_limit('search', max_calls=10, window_seconds=60)
async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for movies command."""
    _cmd_log("searchmovie", update, query=" ".join(context.args) or None)
    track_message(update.effective_user.id, update.message.message_id)

    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /searchmovie <movie name>")
        return

    results = search_media(query, "movie")
    if not results:
        await update.message.reply_text("No movies found.")
        return

    keyboard = []
    for movie in results[:10]:
        title = movie.get("title", "Unknown")
        year = movie.get("releaseDate", "")[:4] if movie.get("releaseDate") else "N/A"
        media_id = movie.get("id")

        # Check availability
        avail_status = is_available(movie, "movie")
        status_emoji = "✅" if avail_status else ""

        keyboard.append([
            InlineKeyboardButton(
                f"{status_emoji} {title} ({year})".strip(),
                callback_data=f"asklib:movie:{media_id}"
            )
        ])

    # Add cancel button
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    sent = await update.message.reply_text(
        "Choose one from the list below:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    track_message(update.effective_user.id, sent.message_id)


@rate_limit('search', max_calls=10, window_seconds=60)
async def search_tv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for TV shows command."""
    _cmd_log("searchtv", update, query=" ".join(context.args) or None)
    track_message(update.effective_user.id, update.message.message_id)

    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /searchtv <tv show name>")
        return

    results = search_media(query, "tv")
    if not results:
        await update.message.reply_text("No TV shows found.")
        return

    keyboard = []
    for show in results[:10]:
        title = show.get("title", "Unknown")
        year = show.get("firstAirDate", "")[:4] if show.get("firstAirDate") else "N/A"
        media_id = show.get("id")

        # Check availability
        avail_status = is_available(show, "tv")
        status_emoji = "✅" if avail_status else ""

        keyboard.append([
            InlineKeyboardButton(
                f"{status_emoji} {title} ({year})".strip(),
                callback_data=f"asklib:tv:{media_id}"
            )
        ])

    # Add cancel button
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    sent = await update.message.reply_text(
        "Choose one from the list below:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    track_message(update.effective_user.id, sent.message_id)


async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's own requests."""
    _cmd_log("myrequests", update)
    user = update.effective_user
    username = f"{user.full_name} (@{user.username})" if user.username else user.full_name

    requests = get_user_requests(username, limit=20)

    if not requests:
        await update.message.reply_text("You haven't made any requests yet.")
        return

    msg_lines = ["*📝 Your Requests:*\n"]
    for req in requests[:10]:
        title = req.get("title", "Unknown")
        req_type = req.get("type", "unknown")
        season = req.get("season")
        timestamp = req.get("timestamp", "")
        priority = req.get("priority", "normal")
        priority_emoji = get_priority_emoji(priority)
        req_id = req.get("id", "")

        line = f"{priority_emoji} #{req_id} {title}"
        if req_type == "tv" and season:
            line += f" (S{season})"
        line += f" - {timestamp[:10]}"
        msg_lines.append(line)

    # Show watchlist count
    counts = get_user_watchlist_count(update.message.chat_id)
    msg_lines.append(f"\n*📊 Pending Watchlist:*")
    msg_lines.append(f"  • Movies: {counts['movies']}/10")
    msg_lines.append(f"  • TV Shows: {counts['tv_shows']}/10")

    await update.message.reply_text(
        "\n".join(msg_lines),
        parse_mode="Markdown"
    )


async def pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all pending requests."""
    _cmd_log("pending", update)
    all_requests = get_all_requests(limit=20)

    if not all_requests:
        await update.message.reply_text("No requests found.")
        return

    msg_lines = ["*⏳ Recent Requests:*\n"]
    for req in all_requests[:15]:
        user = req.get("user", "Unknown")
        title = req.get("title", "Unknown")
        req_type = req.get("type", "unknown")
        season = req.get("season")

        line = f"• {title}"
        if req_type == "tv" and season:
            line += f" (S{season})"
        line += f" by {user}"
        msg_lines.append(line)

    await update.message.reply_text(
        "\n".join(msg_lines),
        parse_mode="Markdown"
    )


async def request_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check request status."""
    _cmd_log("status", update, query=" ".join(context.args) or None)
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /status <title>")
        return

    all_requests = get_all_requests()
    matches = [r for r in all_requests if query.lower() in r.get("title", "").lower()]

    if not matches:
        await update.message.reply_text(f"No requests found for: {query}")
        return

    msg_lines = [f"*📊 Status for '{query}':*\n"]
    for req in matches[:5]:
        title = req.get("title", "Unknown")
        user = req.get("user", "Unknown")
        timestamp = req.get("timestamp", "")

        msg_lines.append(f"• {title}")
        msg_lines.append(f"  Requested by: {user}")
        msg_lines.append(f"  Date: {timestamp[:10]}\n")

    await update.message.reply_text(
        "\n".join(msg_lines),
        parse_mode="Markdown"
    )


@rate_limit('browse', max_calls=3, window_seconds=60)
async def browse_popular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Browse popular/trending movies and TV shows."""
    args = context.args
    media_type = "movie"
    if args and args[0].lower() in ["tv", "shows", "series"]:
        media_type = "tv"
    _cmd_log("browse", update, media_type=media_type)

    try:
        results = get_discover_content(media_type=media_type, page=1)
    except Exception as e:
        logger.error(f"browse_popular error: {e}")
        await update.message.reply_text("❌ Error fetching trending content.")
        return

    if not results:
        await update.message.reply_text("No trending content found.")
        return

    keyboard = []
    for item in results[:15]:
        if media_type == "movie":
            title = item.get("title", "Unknown")
            year = item.get("releaseDate", "")[:4] if item.get("releaseDate") else ""
        else:
            title = item.get("name", "Unknown")
            year = item.get("firstAirDate", "")[:4] if item.get("firstAirDate") else ""

        media_id = item.get("id")
        display = f"{title} ({year})" if year else title

        keyboard.append([
            InlineKeyboardButton(
                display,
                callback_data=f"asklib:{media_type}:{media_id}"
            )
        ])

    type_name = "Movies" if media_type == "movie" else "TV Shows"
    sent = await update.message.reply_text(
        f"🔥 *Trending {type_name}:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    track_message(update.effective_user.id, sent.message_id)


async def backup_database_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger database backup."""
    _cmd_log("backup", update)
    try:
        from backup import backup_database
        result = backup_database()
        if result:
            await update.message.reply_text("✅ Database backed up successfully.")
        else:
            await update.message.reply_text("❌ Backup failed.")
    except Exception as e:
        logger.exception("Backup command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def restore_database_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually restore database from backup."""
    _cmd_log("restore", update, backup_file=(context.args[0] if context.args else None))
    try:
        from backup import restore_database
        backup_file = context.args[0] if context.args else None
        if not backup_file:
            await update.message.reply_text("Usage: /restore <backup_filename>")
            return

        result = restore_database(backup_file)
        if result:
            await update.message.reply_text("✅ Database restored successfully.")
        else:
            await update.message.reply_text("❌ Restore failed.")
    except Exception as e:
        logger.exception("Restore command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def cache_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display cache statistics (admin only)."""
    _cmd_log("cachestats", update)
    try:
        stats = get_cache().get_stats()
        message = (
            f"📊 **Cache Statistics**\n\n"
            f"**Entries:** {stats['entries']}\n"
            f"**Hits:** {stats['hits']}\n"
            f"**Misses:** {stats['misses']}\n"
            f"**Total Requests:** {stats['total_requests']}\n"
            f"**Hit Rate:** {stats['hit_rate']}\n\n"
            f"Higher hit rate = fewer API calls = faster responses"
        )
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Cache stats command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def cache_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all cache entries (admin only)."""
    _cmd_log("cacheclear", update)
    try:
        get_cache().clear()
        await update.message.reply_text("✅ Cache cleared successfully.")
    except Exception as e:
        logger.exception("Cache clear command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def set_priority_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set priority for a request (admin only)."""
    _cmd_log("setpriority", update,
             request_id=(context.args[0] if context.args else None),
             priority=(context.args[1] if context.args and len(context.args) > 1 else None))
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /setpriority <request_id> <high|normal|low>\n\n"
            "Example: /setpriority 123 high"
        )
        return

    try:
        request_id = int(context.args[0])
        priority = context.args[1].lower()

        if priority not in ["high", "normal", "low"]:
            await update.message.reply_text(
                "❌ Invalid priority. Use: high, normal, or low"
            )
            return

        success = set_request_priority(request_id, priority)

        if success:
            emoji = get_priority_emoji(priority)
            await update.message.reply_text(
                f"✅ Priority set to {emoji} **{priority.upper()}** for request #{request_id}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ Failed to set priority. Check if request ID exists."
            )

    except ValueError:
        await update.message.reply_text("❌ Invalid request ID. Must be a number.")
    except Exception as e:
        logger.exception("Set priority command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def priority_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending requests organized by priority."""
    _cmd_log("priorityqueue", update,
             limit=(context.args[0] if context.args else None))
    try:
        # Get limit from args or default to 20
        limit = 20
        if context.args and context.args[0].isdigit():
            limit = int(context.args[0])
            limit = min(limit, 50)  # Cap at 50

        requests_by_priority = get_pending_requests_by_priority(limit)

        message_lines = ["📋 **Priority Queue**\n"]

        # High priority
        high_requests = requests_by_priority.get("high", [])
        if high_requests:
            message_lines.append(f"🔴 **HIGH PRIORITY** ({len(high_requests)})")
            for req in high_requests[:10]:  # Show max 10 per priority
                title = req.get("title", "Unknown")
                req_id = req.get("id", "?")
                user = req.get("user", "Unknown")
                season_text = f" S{req['season']}" if req.get("season") else ""
                message_lines.append(f"  #{req_id}: {title}{season_text} ({user})")
            if len(high_requests) > 10:
                message_lines.append(f"  ... and {len(high_requests) - 10} more")
            message_lines.append("")

        # Normal priority
        normal_requests = requests_by_priority.get("normal", [])
        if normal_requests:
            message_lines.append(f"⚪ **NORMAL PRIORITY** ({len(normal_requests)})")
            for req in normal_requests[:10]:
                title = req.get("title", "Unknown")
                req_id = req.get("id", "?")
                user = req.get("user", "Unknown")
                season_text = f" S{req['season']}" if req.get("season") else ""
                message_lines.append(f"  #{req_id}: {title}{season_text} ({user})")
            if len(normal_requests) > 10:
                message_lines.append(f"  ... and {len(normal_requests) - 10} more")
            message_lines.append("")

        # Low priority
        low_requests = requests_by_priority.get("low", [])
        if low_requests:
            message_lines.append(f"🔵 **LOW PRIORITY** ({len(low_requests)})")
            for req in low_requests[:10]:
                title = req.get("title", "Unknown")
                req_id = req.get("id", "?")
                user = req.get("user", "Unknown")
                season_text = f" S{req['season']}" if req.get("season") else ""
                message_lines.append(f"  #{req_id}: {title}{season_text} ({user})")
            if len(low_requests) > 10:
                message_lines.append(f"  ... and {len(low_requests) - 10} more")
            message_lines.append("")

        if not high_requests and not normal_requests and not low_requests:
            message_lines.append("No pending requests.")
        else:
            total = len(high_requests) + len(normal_requests) + len(low_requests)
            message_lines.append(f"**Total:** {total} pending requests")

        message = "\n".join(message_lines)
        await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Priority queue command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def notification_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's notification history."""
    _cmd_log("notifications", update)
    try:
        notification_manager = get_notification_manager()
        chat_id = update.message.chat_id

        history = notification_manager.get_notification_history(chat_id, limit=15)

        if not history:
            await update.message.reply_text(
                "📭 No notification history yet.\n\n"
                "You'll receive notifications when your requested items become available."
            )
            return

        message_lines = ["📬 **Recent Notifications**\n"]

        for notif in reversed(history):  # Show most recent first
            title = notif.get("title", "Unknown")
            media_type = notif.get("media_type", "unknown")
            timestamp = notif.get("timestamp", "")
            status = notif.get("status", "delivered")

            # Format timestamp
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%b %d, %H:%M")
            except:
                time_str = "Unknown time"

            icon = "🎬" if media_type == "movie" else "📺"
            status_icon = "✅" if status == "delivered" else "❌"

            message_lines.append(f"{icon} {status_icon} **{title}**")
            message_lines.append(f"   {time_str}")
            message_lines.append("")

        message = "\n".join(message_lines)
        await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Notification history command failed")
        await update.message.reply_text(f"❌ Error: {e}")


async def delete_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle delete request callback."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("delreq:"):
        return

    parts = data.split(":", 3)
    if len(parts) < 4:
        return

    media_id, media_type, season = parts[1], parts[2], parts[3]

    try:
        media_id = int(media_id)
        season = int(season) if season != "None" else None
    except Exception:
        await query.edit_message_text("❌ Invalid request format.")
        return

    # Remove from watchlist
    success = remove_from_watchlist(media_id, media_type, season=season)
    _event_log("request_deleted_from_watchlist", update,
               media_id=media_id, media_type=media_type, season=season, success=success)

    if success:
        await query.edit_message_text("✅ Request removed from watchlist.")
    else:
        await query.edit_message_text("❌ Failed to remove request.")


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger data cleanup."""
    _cmd_log("cleanup", update)
    try:
        from data_cleanup import cleanup_all

        await update.message.reply_text("🧹 Running data cleanup...")

        results = cleanup_all()

        # Build status message
        watchlist_removed = results["watchlist"]["removed_failed"] + results["watchlist"]["removed_checking"]
        request_log_removed = results["request_log"]["removed_json"] + results["request_log"]["removed_postgres"]

        message_lines = [
            "✅ *Data Cleanup Complete*\n",
            f"**Watchlist:**",
            f"  • Removed {results['watchlist']['removed_failed']} failed entries",
            f"  • Removed {results['watchlist']['removed_checking']} stale checking entries",
            f"  • Kept {results['watchlist']['kept']} active entries\n",
            f"**Request Log:**",
            f"  • Removed {results['request_log']['removed_json']} old JSON entries",
            f"  • Removed {results['request_log']['removed_postgres']} old PostgreSQL entries",
            f"  • Kept {results['request_log']['kept_json']} JSON entries",
            f"  • Kept {results['request_log']['kept_postgres']} PostgreSQL entries\n",
            f"**Total removed:** {watchlist_removed + request_log_removed} entries"
        ]

        await update.message.reply_text("\n".join(message_lines), parse_mode="Markdown")

    except Exception as e:
        logger.exception("Cleanup command failed")
        await update.message.reply_text(f"❌ Cleanup failed: {e}")
