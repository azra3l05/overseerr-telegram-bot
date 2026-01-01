# handlers.py
"""
All Telegram command and callback handlers for the Overseerr bot.
"""
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from collections import Counter

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import InlineQueryResultArticle, InputTextMessageContent, InlineQueryResultPhoto
from telegram.ext import ContextTypes

import requests

from overseerr_api import search_media, get_details, request_media
from config import LIBRARIES_MOVIES, LIBRARIES_TV, TMDB_API_KEY
from database import log_request, get_user_requests, get_all_requests, add_to_watchlist, remove_from_watchlist
from utils import (
    safe_year, title_with_year_from_details, is_available,
    imdb_url_from_details, get_tmdb_details_or_none, tmdb_search
)

# Aliases for backward compatibility
MOVIE_LIBRARIES = LIBRARIES_MOVIES
TV_LIBRARIES = LIBRARIES_TV

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State & Message Tracking
# ---------------------------------------------------------------------------
user_context: Dict[int, Dict[str, Any]] = {}  # ephemeral per-user flow state


def track_message(user_id: int, message_id: int):
    """Track message IDs for cleanup."""
    if user_id not in user_context:
        user_context[user_id] = {}
    user_context[user_id].setdefault("messages", []).append(message_id)


async def cleanup_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Clean up tracked messages for a user."""
    for mid in user_context.get(user_id, {}).get("messages", []):
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    if user_id in user_context:
        user_context[user_id]["messages"] = []


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """Job to auto-delete a message after delay."""
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception:
        pass


async def schedule_autodelete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, seconds: int = 3):
    """Schedule a message for auto-deletion."""
    context.job_queue.run_once(
        _delete_message_job,
        when=seconds,
        data={"chat_id": chat_id, "message_id": message_id},
        name=f"del:{chat_id}:{message_id}"
    )


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message."""
    user = update.effective_user
    first_name = user.first_name if user and user.first_name else "there"

    welcome_text = (
        f"👋 **Hey {first_name}!**\n\n"
        "I'm your *Overseerr Telegram Assistant* — here to help you find, request, "
        "and keep track of movies and TV shows from your media server.\n\n"
        "🎬 **Here's what I can do:**\n"
        "• `/searchmovie <title>` – Find and request movies\n"
        "• `/searchtv <title>` – Find and request TV shows\n"
        "• `/browse` – Browse trending movies (use `/browse tv` for shows)\n"
        "• Tap **✅ Request** on posters to add items directly to Overseerr\n"
        "• Tap **🔁 Recommendations** for similar titles\n"
        "• Tap **⭐ IMDb / 📂 TMDB** links for details\n\n"
        "📋 **Track Your Requests:**\n"
        "• `/myrequests` – View all your requests with cancel option\n"
        "• `/pending` – See what you're waiting for\n"
        "• `/status` – Check download/processing status\n\n"
        "🧠 **Smart features:**\n"
        "• Detects if something is already in your library\n"
        "• Sends notifications when new requests become available\n"
        "• Shows rich movie cards with posters, genres, and ratings\n\n"
        "Ready to start? Search for something now 👇\n"
        "`/searchmovie Dune`\n"
    )

    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show enhanced bot statistics (admin only)."""
    from config import ADMIN_USER_IDS
    from datetime import datetime, timedelta
    
    # Check if user is admin
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only available to administrators.")
        return
    
    try:
        logs = get_all_requests()
        
        if not logs:
            await update.message.reply_text("📊 No statistics available yet.")
            return
        
        total = len(logs)
        
        # User statistics
        users = Counter([r.get("user", "Unknown") for r in logs])
        unique_users = len(users)
        top_users = users.most_common(5)
        
        # Media type breakdown
        movie_count = sum(1 for r in logs if r.get("type") == "movie")
        tv_count = sum(1 for r in logs if r.get("type") == "tv")
        
        # Library statistics
        libraries = Counter([r.get("library", "Unknown") for r in logs])
        top_libraries = libraries.most_common(5)
        
        # Popular requests
        titles = Counter([r.get("title", "Unknown") for r in logs])
        popular_titles = titles.most_common(5)
        
        # Recent activity (last 7 days)
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        recent_requests = []
        
        for r in logs:
            try:
                timestamp_str = r.get("timestamp", "")
                if timestamp_str:
                    # Parse timestamp (format: "2026-01-01 12:00:00")
                    req_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    if req_time >= week_ago:
                        recent_requests.append(r)
            except Exception:
                pass
        
        recent_count = len(recent_requests)
        
        # Build message
        message = "📊 *Bot Statistics Dashboard*\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += "*📈 Overview*\n"
        message += f"• Total Requests: `{total}`\n"
        message += f"• Unique Users: `{unique_users}`\n"
        message += f"• Movies: `{movie_count}` ({movie_count*100//total if total else 0}%)\n"
        message += f"• TV Shows: `{tv_count}` ({tv_count*100//total if total else 0}%)\n"
        message += f"• Last 7 Days: `{recent_count}` requests\n\n"
        
        message += "*👥 Top Users*\n"
        for i, (user, count) in enumerate(top_users, 1):
            # Truncate long usernames and escape markdown special chars
            display_name = user[:30] + "..." if len(user) > 30 else user
            # Escape markdown special characters
            display_name = display_name.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
            message += f"{i}. {display_name}: `{count}` requests\n"
        message += "\n"
        
        message += "*📚 Top Libraries*\n"
        for i, (lib, count) in enumerate(top_libraries, 1):
            message += f"{i}. {lib}: `{count}` requests\n"
        message += "\n"
        
        message += "*🔥 Most Requested*\n"
        for i, (title, count) in enumerate(popular_titles, 1):
            display_title = title[:35] + "..." if len(title) > 35 else title
            # Escape markdown special characters in titles
            display_title = display_title.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
            if count > 1:
                message += f"{i}. {display_title} (`{count}x`)\n"
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.exception("Error generating stats")
        await update.message.reply_text("❌ Error generating statistics.")




async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for movies command."""
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
    lines = []
    for r in results[:5]:
        year = safe_year(r.get("releaseDate") or r.get("release_date"))
        rating = r.get("voteAverage")
        overview = r.get("overview", "")
        
        # Format rating
        rating_str = f"⭐️ {rating:.1f}" if rating else ""
        
        # Title with year
        title_line = f"*{r.get('title', 'Unknown')}*" + (f" ({year})" if year else "")
        
        # Build result line
        result_parts = [title_line]
        if rating_str:
            result_parts.append(rating_str)
        
        # Truncate overview to fit
        if overview:
            max_overview_length = 100
            if len(overview) > max_overview_length:
                overview = overview[:max_overview_length].rsplit(' ', 1)[0] + "..."
            result_parts.append(f"_{overview}_")
        
        lines.append(" ".join(result_parts) if len(result_parts) == 1 else "\n".join(result_parts))
        lines.append("")  # Empty line between results
        
        # Button label
        button_label = f"{r.get('title', 'Unknown')}" + (f" ({year})" if year else "")
        keyboard.append([InlineKeyboardButton(button_label, callback_data=f"movie:{r['id']}")])

    sent_text = await update.message.reply_text(
        "🎬 *Search Results:*\n\n" + "\n".join(lines),
        parse_mode="Markdown"
    )
    track_message(update.effective_user.id, sent_text.message_id)

    sent = await update.message.reply_text(
        "Choose one from the list below:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    track_message(update.effective_user.id, sent.message_id)


async def search_tv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for TV shows command."""
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
    lines = []
    for r in results[:5]:
        year = safe_year(r.get("firstAirDate") or r.get("first_air_date"))
        rating = r.get("voteAverage")
        overview = r.get("overview", "")
        
        # Format rating
        rating_str = f"⭐️ {rating:.1f}" if rating else ""
        
        # Title with year
        title_line = f"*{r.get('title', 'Unknown')}*" + (f" ({year})" if year else "")
        
        # Build result line
        result_parts = [title_line]
        if rating_str:
            result_parts.append(rating_str)
        
        # Truncate overview to fit
        if overview:
            max_overview_length = 100
            if len(overview) > max_overview_length:
                overview = overview[:max_overview_length].rsplit(' ', 1)[0] + "..."
            result_parts.append(f"_{overview}_")
        
        lines.append(" ".join(result_parts) if len(result_parts) == 1 else "\n".join(result_parts))
        lines.append("")  # Empty line between results

        # Button label
        button_label = f"{r.get('title', 'Unknown')}" + (f" ({year})" if year else "")
        keyboard.append([InlineKeyboardButton(button_label, callback_data=f"tv:{r['id']}")])

    sent_text = await update.message.reply_text(
        "📺 *Search Results:*\n\n" + "\n".join(lines),
        parse_mode="Markdown"
    )
    track_message(update.effective_user.id, sent_text.message_id)

    sent = await update.message.reply_text(
        "Choose one from the list below:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    track_message(update.effective_user.id, sent.message_id)


async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's recent requests with option to cancel."""
    telegram_user = f"{update.effective_user.full_name} (@{update.effective_user.username})" \
        if update.effective_user.username else update.effective_user.full_name

    user_requests = get_user_requests(telegram_user, limit=10)

    if not user_requests:
        await update.message.reply_text("You have no requests recorded via Telegram.")
        return

    # Build message with inline buttons for cancelling
    msg_lines = ["📋 Your Requests:\n"]
    kb = []
    
    logger.info(f"User requests data: {user_requests}")
    
    for r in user_requests:
        req_id = r.get('id')
        season_text = f" (Season {r.get('season')})" if r.get("season") else ""
        msg_lines.append(f"• {r.get('title')}{season_text}")
        msg_lines.append(f"  {r.get('type').upper()} | {r.get('library')} | {r.get('timestamp')}")
        
        # Add cancel button for each request
        logger.info(f"Request ID for '{r.get('title')}': {req_id}")
        if req_id:
            kb.append([InlineKeyboardButton(
                f"🗑 Cancel: {r.get('title')[:30]}",
                callback_data=f"delreq:{req_id}"
            )])
    
    msg_lines.append("\n💡 Click a button below to cancel a request")
    
    await update.message.reply_text(
        "\n".join(msg_lines),
        reply_markup=InlineKeyboardMarkup(kb) if kb else None
    )


async def pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's pending requests that haven't become available yet."""
    from database import get_watchlist
    
    watchlist = get_watchlist()
    chat_id = update.message.chat_id
    
    # Filter watchlist to this chat
    user_pending = [w for w in watchlist if w.get("chat_id") == chat_id]
    
    if not user_pending:
        await update.message.reply_text("🎉 You have no pending requests! Everything you've requested is either available or not being tracked.")
        return
    
    msg_lines = ["⏳ *Your Pending Requests:*\n"]
    for w in user_pending:
        title = w.get("title", "Unknown")
        media_type = w.get("media_type", "")
        season = w.get("season")
        library = w.get("library_name", "")
        
        emoji = "🎬" if media_type == "movie" else "📺"
        season_text = f" (Season {season})" if season else ""
        
        msg_lines.append(f"{emoji} *{title}*{season_text}")
        msg_lines.append(f"   Library: {library}")
        msg_lines.append("")
    
    msg_lines.append("💡 You'll be notified when these become available!")
    
    await update.message.reply_text(
        "\n".join(msg_lines),
        parse_mode="Markdown"
    )


async def request_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed status updates from Overseerr for user's own requests only."""
    from overseerr_api import get_request_status
    
    # Get only this user's requests
    telegram_user = f"{update.effective_user.full_name} (@{update.effective_user.username})" \
        if update.effective_user.username else update.effective_user.full_name

    user_requests = get_user_requests(telegram_user, limit=10)

    if not user_requests:
        await update.message.reply_text("You have no requests recorded.")
        return
    
    msg_lines = ["📊 *Request Status Updates:*\n"]
    
    for r in user_requests:
        overseerr_req_id = r.get('overseerr_request_id')
        title = r.get('title', 'Unknown')
        season_text = f" (Season {r.get('season')})" if r.get("season") else ""
        
        if not overseerr_req_id:
            msg_lines.append(f"• *{title}*{season_text}")
            msg_lines.append(f"  ⚠️ No Overseerr ID - cannot check status")
            msg_lines.append("")
            continue
        
        try:
            status_data = get_request_status(overseerr_req_id)
            status = status_data.get("status", "unknown")
            
            # Map status codes to readable text
            status_map = {
                1: "⏳ Pending Approval",
                2: "✅ Approved",
                3: "❌ Declined",
                4: "🎉 Available"
            }
            status_text = status_map.get(status, f"Status: {status}")
            
            # Check media status
            media_info = status_data.get("media", {})
            media_status = media_info.get("status", "unknown")
            
            media_status_map = {
                1: "Unknown",
                2: "📥 Pending",
                3: "⚙️ Processing",
                4: "⬇️ Partially Available",
                5: "✅ Available"
            }
            media_text = media_status_map.get(media_status, f"Media: {media_status}")
            
            msg_lines.append(f"• *{title}*{season_text}")
            msg_lines.append(f"  {status_text}")
            msg_lines.append(f"  {media_text}")
            msg_lines.append("")
            
        except Exception as e:
            logger.exception(f"Failed to get status for request {overseerr_req_id}")
            msg_lines.append(f"• *{title}*{season_text}")
            msg_lines.append(f"  ⚠️ Could not fetch status")
            msg_lines.append("")
    
    await update.message.reply_text(
        "\n".join(msg_lines),
        parse_mode="Markdown"
    )


async def browse_popular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Browse popular/trending movies and TV shows."""
    from overseerr_api import get_discover_content
    
    # Check if user specified movie or tv, default to movies
    args = context.args
    media_type = "movie"
    if args and args[0].lower() in ["tv", "shows", "series"]:
        media_type = "tv"
    
    try:
        results = get_discover_content(media_type=media_type, page=1)
    except Exception as e:
        logger.exception("Failed to get discover content")
        await update.message.reply_text(f"❌ Failed to fetch trending content: {str(e)}")
        return
    
    if not results:
        await update.message.reply_text("No trending content found.")
        return
    
    keyboard = []
    lines = []
    
    emoji = "🎬" if media_type == "movie" else "📺"
    lines.append(f"*{emoji} Trending {'Movies' if media_type == 'movie' else 'TV Shows'}:*\n")
    
    for r in results[:10]:
        year = safe_year(r.get("releaseDate") or r.get("firstAirDate"))
        rating = r.get("voteAverage")
        overview = r.get("overview", "")
        
        # Format rating
        rating_str = f"⭐️ {rating:.1f}" if rating else ""
        
        # Title with year
        title_line = f"*{r.get('title', 'Unknown')}*" + (f" ({year})" if year else "")
        
        # Build result line
        result_parts = [title_line]
        if rating_str:
            result_parts.append(rating_str)
        
        # Truncate overview to fit
        if overview:
            max_overview_length = 100
            if len(overview) > max_overview_length:
                overview = overview[:max_overview_length].rsplit(' ', 1)[0] + "..."
            result_parts.append(f"_{overview}_")
        
        lines.append(" ".join(result_parts) if len(result_parts) == 1 else "\n".join(result_parts))
        lines.append("")  # Empty line between results
        
        # Button label
        button_label = f"{r.get('title', 'Unknown')}" + (f" ({year})" if year else "")
        callback_type = "movie" if media_type == "movie" else "tv"
        keyboard.append([InlineKeyboardButton(button_label, callback_data=f"{callback_type}:{r['id']}")])
    
    lines.append(f"\n💡 Tip: Use `/browse {'tv' if media_type == 'movie' else 'movie'}` to see the other type")
    
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )
    
    await update.message.reply_text(
        "Choose one to request:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def backup_database_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create database backup (admin only)."""
    from config import ADMIN_USER_IDS
    from backup import create_backup, list_backups
    
    # Check if user is admin
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only available to administrators.")
        return
    
    await update.message.reply_text("⏳ Creating backup...")
    
    backup_path = create_backup()
    
    if backup_path:
        backup_name = os.path.basename(backup_path)
        backups = list_backups()
        await update.message.reply_text(
            f"✅ Backup created successfully!\n\n"
            f"📁 Backup: `{backup_name}`\n"
            f"📊 Total backups: {len(backups)}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Backup failed. Check logs for details.")


async def restore_database_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restore database from backup (admin only)."""
    from config import ADMIN_USER_IDS
    from backup import restore_backup, list_backups
    
    # Check if user is admin
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only available to administrators.")
        return
    
    backups = list_backups()
    
    if not backups:
        await update.message.reply_text("❌ No backups available.")
        return
    
    # If no backup name provided, show list
    if not context.args:
        msg_lines = ["📋 *Available Backups:*\n"]
        for backup in backups[:10]:
            msg_lines.append(f"• `{backup}`")
        msg_lines.append(f"\n💡 Use `/restore <backup_name>` to restore")
        await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")
        return
    
    backup_name = context.args[0]
    
    if backup_name not in backups:
        await update.message.reply_text(f"❌ Backup '{backup_name}' not found.")
        return
    
    await update.message.reply_text("⏳ Restoring backup...")
    
    success = restore_backup(backup_name)
    
    if success:
        await update.message.reply_text(
            f"✅ Database restored from `{backup_name}`\n\n"
            f"⚠️ Restart the bot for changes to take full effect.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Restore failed. Check logs for details.")


async def delete_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle request deletion."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("delreq:"):
        return

    try:
        req_id = int(data.split(":")[1])
    except Exception:
        await query.edit_message_text("❌ Invalid request ID.")
        return

    telegram_user = f"{query.from_user.full_name} (@{query.from_user.username})" \
        if query.from_user.username else query.from_user.full_name

    # Get request details
    user_requests = get_user_requests(telegram_user, limit=100)
    request_to_delete = next((r for r in user_requests if r.get('id') == req_id), None)
    
    if not request_to_delete:
        await query.edit_message_text("❌ Request not found or doesn't belong to you.")
        return

    title = request_to_delete.get('title')
    overseerr_request_id = request_to_delete.get('overseerr_request_id')
    
    # Try to delete from Overseerr
    deleted_from_overseerr = False
    if overseerr_request_id:
        try:
            from overseerr_api import delete_request
            delete_request(overseerr_request_id)
            deleted_from_overseerr = True
            logger.info(f"Deleted Overseerr request {overseerr_request_id} for {title}")
        except Exception as e:
            logger.warning(f"Could not delete from Overseerr: {e}")

    # Remove from watchlist
    media_id = request_to_delete.get('tmdb_id')
    media_type = request_to_delete.get('type')
    season = request_to_delete.get('season')
    if media_id and media_type:
        try:
            remove_from_watchlist(media_id, media_type, season=season)
        except Exception as e:
            logger.warning(f"Could not remove from watchlist: {e}")

    # Send success message
    if deleted_from_overseerr:
        await query.edit_message_text(
            f"✅ Successfully cancelled: *{title}*\n\n"
            f"The request has been removed from Overseerr and Radarr/Sonarr.",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"⚠️ Stopped tracking: *{title}*\n\n"
            f"Could not remove from Overseerr (request may have already been processed), "
            f"but you'll no longer receive notifications.",
            parse_mode="Markdown"
        )



async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle media selection buttons (movie: or tv: callbacks)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data:
        return

    if ":" not in data:
        return

    parts = data.split(":", 1)
    media_type, media_id = parts[0], parts[1]

    try:
        media_id = int(media_id)
    except Exception:
        pass

    try:
        details = get_details(media_id, media_type)
        if not details:
            await query.edit_message_text("Could not fetch details from Overseerr.")
            return

        await send_rich_poster(
            query.message.chat_id,
            details,
            media_type,
            context,
            user_id=query.from_user.id
        )

    except requests.exceptions.HTTPError as he:
        logger.exception("button_handler: Overseerr error for id=%s type=%s", media_id, media_type)
        if TMDB_API_KEY:
            try:
                tmdb_details = get_tmdb_details_or_none(media_id, media_type)
                if tmdb_details:
                    await send_rich_poster(
                        query.message.chat_id,
                        tmdb_details,
                        media_type,
                        context,
                        user_id=query.from_user.id
                    )
                    return
            except Exception:
                pass

        await query.edit_message_text(
            "Sorry - I couldn't fetch details from Overseerr for that item. Try again later."
        )

    except Exception as e:
        logger.exception("button_handler error: %s", e)
        await query.edit_message_text("An error occurred. Please try again.")


async def send_rich_poster(chat_id: int, details: dict, media_type: str, context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None):
    """Send a rich poster message with inline buttons."""
    title = details.get("title") or details.get("name") or "Unknown"
    year = safe_year(details.get("releaseDate") or details.get("release_date") or details.get("firstAirDate") or details.get("first_air_date"))
    tagline = details.get("tagline") or ""
    overview = (details.get("overview") or "")[:700]
    runtime = details.get("runtime") or details.get("episodeRunTime") or None
    runtime_text = f" • {runtime} min" if runtime else ""
    rating = details.get("rating") or details.get("voteAverage") or details.get("vote_average")
    rating_text = f" • ⭐ {rating}" if rating else ""
    genres = details.get("genres") or []
    
    if isinstance(genres, list):
        if genres and isinstance(genres[0], dict):
            genre_text = ", ".join(g.get("name") for g in genres if g.get("name"))
        else:
            genre_text = ", ".join(genres)
    else:
        genre_text = ""

    caption_lines = []
    caption_lines.append(f"*{title}*" + (f" ({year})" if year else ""))
    meta = []
    if genre_text:
        meta.append(genre_text)
    if runtime_text:
        meta.append(runtime_text.strip())
    if rating_text:
        meta.append(rating_text.strip())
    if meta:
        caption_lines.append(" • ".join(meta))
    if tagline:
        caption_lines.append(f"_{tagline}_")
    caption_lines.append(overview)
    caption = "\n\n".join(caption_lines)

    poster_url = None
    if details.get("posterPath"):
        poster_url = f"https://image.tmdb.org/t/p/w500{details.get('posterPath')}"

    # Buttons
    kb = []
    kb.append([InlineKeyboardButton("✅ Request", callback_data=f"asklib:{media_type}:{details.get('id')}")])

    # IMDb link
    imdb_id = None
    ext = details.get("externalIds") or details.get("external_ids") or {}
    imdb_id = details.get("imdbId") or ext.get("imdb_id") or ext.get("imdbId") or ext.get("imdb")
    if imdb_id:
        imdb_url = imdb_id if imdb_id.startswith("http") else f"https://www.imdb.com/title/{imdb_id}"
        kb.append([InlineKeyboardButton("⭐ IMDb", url=imdb_url)])

    # TMDB page
    tmdb_id = details.get("tmdbId") or details.get("tmdb_id") or details.get("id")
    if tmdb_id:
        if media_type == "movie":
            tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
        else:
            tmdb_url = f"https://www.themoviedb.org/tv/{tmdb_id}"
        kb.append([InlineKeyboardButton("📂 TMDB", url=tmdb_url)])

    # Trailer
    trailer_url = None
    videos = details.get("videos") or details.get("video") or {}
    if isinstance(videos, dict):
        for v in videos.get("results", [])[:5]:
            if v.get("site", "").lower() == "youtube" and v.get("type", "").lower() in ("trailer", "teaser"):
                trailer_url = f"https://www.youtube.com/watch?v={v.get('key')}"
                break
    if trailer_url:
        kb.append([InlineKeyboardButton("🎞 Watch trailer", url=trailer_url)])

    # Recommendations
    kb.append([InlineKeyboardButton("🔁 Recommendations", callback_data=f"recommend:{media_type}:{details.get('id')}")])

    try:
        if poster_url:
            sent = await context.bot.send_photo(
                chat_id,
                poster_url,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        else:
            sent = await context.bot.send_message(
                chat_id,
                caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb),
            )

        if user_id:
            track_message(user_id, sent.message_id)

    except Exception as e:
        logger.exception("Error sending rich poster: %s", e)
        try:
            sent = await context.bot.send_message(
                chat_id,
                f"*{title}*\n\n{overview}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb),
            )
            if user_id:
                track_message(user_id, sent.message_id)
        except Exception:
            pass


async def asklib_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'asklib:' callback - prompts user to select library."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("asklib:"):
        return

    parts = data.split(":", 2)
    if len(parts) < 3:
        return

    media_type, media_id = parts[1], parts[2]

    try:
        media_id = int(media_id)
    except Exception:
        pass

    await ask_library(query, media_type, media_id)


async def ask_library(query, media_type: str, media_id):
    """Prompt user to select which library to add media to."""
    if media_type == "movie":
        libs = MOVIE_LIBRARIES
        lib_type_label = "Movie"
    else:
        libs = TV_LIBRARIES
        lib_type_label = "TV"

    if not libs:
        await query.message.reply_text(f"No {lib_type_label} libraries configured.")
        return

    # Build library selection buttons
    # libs is a dict: {name: id}
    kb = []
    for lib_name, lib_id in libs.items():
        kb.append([InlineKeyboardButton(lib_name, callback_data=f"lib:{media_type}:{media_id}:{lib_id}")])

    # Send new message instead of editing (since original might be photo)
    sent = await query.message.reply_text(
        f"Select a {lib_type_label} library:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    track_message(query.from_user.id, sent.message_id)


async def library_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle library selection (lib: callback)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("lib:"):
        return

    parts = data.split(":", 3)
    if len(parts) < 4:
        return

    media_type, media_id, library_id = parts[1], parts[2], parts[3]

    try:
        media_id = int(media_id)
        library_id = int(library_id)
    except Exception:
        pass

    # For TV shows, ask for season selection
    if media_type == "tv":
        try:
            details = get_details(media_id, "tv")
            seasons = details.get("seasons") or []
            if not seasons:
                await query.edit_message_text("No seasons found for this show.")
                return

            # Build season selection buttons
            kb = []
            for s in seasons:
                season_num = s.get("seasonNumber")
                if season_num is None:
                    continue
                season_label = f"Season {season_num}"
                kb.append([InlineKeyboardButton(season_label, callback_data=f"season:{media_id}:{library_id}:{season_num}")])

            await query.edit_message_text(
                "Select a season:",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        except Exception as e:
            logger.exception("library_handler error fetching TV details: %s", e)
            await query.edit_message_text("Error fetching show details. Please try again.")

    else:
        # Movies: proceed directly to confirmation
        try:
            details = get_details(media_id, "movie")
            title = title_with_year_from_details(details, "movie")

            # Find library name - MOVIE_LIBRARIES is {name: id}
            lib_name = "Unknown"
            for name, lid in MOVIE_LIBRARIES.items():
                if lid == library_id:
                    lib_name = name
                    break

            kb = [
                [
                    InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:movie:{media_id}:{library_id}:0"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel")
                ]
            ]

            await query.edit_message_text(
                f"Confirm request:\n\n*{title}*\nLibrary: {lib_name}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        except Exception as e:
            logger.exception("library_handler error for movie: %s", e)
            await query.edit_message_text("Error processing your request. Please try again.")



async def season_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle season selection for TV shows."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("season:"):
        return

    parts = data.split(":", 3)
    if len(parts) < 4:
        return

    media_id, library_id, season = parts[1], parts[2], parts[3]

    try:
        media_id = int(media_id)
        library_id = int(library_id)
        season = int(season)
    except Exception:
        pass

    try:
        details = get_details(media_id, "tv")
        title = title_with_year_from_details(details, "tv")

        # Find library name - TV_LIBRARIES is {name: id}
        lib_name = "Unknown"
        for name, lid in TV_LIBRARIES.items():
            if lid == library_id:
                lib_name = name
                break

        kb = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:tv:{media_id}:{library_id}:{season}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel")
            ]
        ]

        await query.edit_message_text(
            f"Confirm request:\n\n*{title}*\nSeason: {season}\nLibrary: {lib_name}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except Exception as e:
        logger.exception("season_handler error: %s", e)
        await query.edit_message_text("Error processing your request. Please try again.")


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirmation of media request."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("confirm:"):
        return

    parts = data.split(":", 4)
    if len(parts) < 5:
        return

    media_type, media_id, library_id, season = parts[1], parts[2], parts[3], parts[4]

    try:
        media_id = int(media_id)
        library_id = int(library_id)
        season = int(season) if season != "0" else None
    except Exception:
        pass

    telegram_user = f"{query.from_user.full_name} (@{query.from_user.username})" \
        if query.from_user.username else query.from_user.full_name

    try:
        # Submit request to Overseerr
        if media_type == "movie":
            response = request_media(media_id, "movie", library_id=library_id)
        else:
            response = request_media(media_id, "tv", seasons=[season], library_id=library_id)

        # Extract Overseerr request ID from response
        overseerr_request_id = response.get("id")

        details = get_details(media_id, media_type)
        title = title_with_year_from_details(details, media_type)

        # Find library name - libraries are {name: id}
        if media_type == "movie":
            lib_name = next((name for name, lid in MOVIE_LIBRARIES.items() if lid == library_id), "Unknown")
        else:
            lib_name = next((name for name, lid in TV_LIBRARIES.items() if lid == library_id), "Unknown")

        # Log to database
        log_request(
            telegram_user=telegram_user,
            media_title=title,
            media_type=media_type,
            season=season,
            library_name=lib_name,
            tmdb_id=media_id,
            overseerr_request_id=overseerr_request_id
        )

        # Add to watchlist for availability checking
        add_to_watchlist(
            media_id=media_id,
            media_type=media_type,
            chat_id=query.message.chat_id,
            title=title,
            library_name=lib_name,
            season=season
        )

        # Check immediate availability from Postgres
        try:
            from postgres_checker import get_postgres_checker
            pg_checker = get_postgres_checker()
            logger.info(f"Postgres checker status: {pg_checker is not None}")
            
            if pg_checker:
                is_available = False
                if media_type == "movie":
                    logger.info(f"Checking movie availability for tmdb_id={media_id}")
                    is_available, movie_data = pg_checker.check_movie_availability(media_id)
                    logger.info(f"Movie availability result: is_available={is_available}, data={movie_data}")
                    if is_available:
                        success_msg = f"🎉 *{title}* is already available in the {lib_name} library!"
                else:
                    # For TV, check if it's available (season checking is complex, check show level)
                    details_for_tvdb = get_details(media_id, "tv")
                    tvdb_id = details_for_tvdb.get("externalIds", {}).get("tvdbId")
                    logger.info(f"Checking TV availability for tvdb_id={tvdb_id}, season={season}")
                    if tvdb_id:
                        is_available, tv_data = pg_checker.check_tv_availability(tvdb_id=tvdb_id, season_number=season)
                        logger.info(f"TV availability result: is_available={is_available}")
                        if is_available:
                            success_msg = f"🎉 *{title}*"
                            if season:
                                success_msg += f" (Season {season})"
                            success_msg += f" is already available in the {lib_name} library!"
                
                if not is_available:
                    success_msg = f"✅ Successfully requested: *{title}*"
                    if media_type == "tv" and season:
                        success_msg += f" (Season {season})"
                    success_msg += f"\n\nYou'll be notified when it's available!"
            else:
                logger.warning("Postgres checker not available")
                success_msg = f"✅ Successfully requested: *{title}*"
                if media_type == "tv" and season:
                    success_msg += f" (Season {season})"
                success_msg += f"\n\nYou'll be notified when it's available!"
        except Exception as e:
            logger.exception(f"Could not check immediate availability: {e}")
            success_msg = f"✅ Successfully requested: *{title}*"
            if media_type == "tv" and season:
                success_msg += f" (Season {season})"
            success_msg += f"\n\nYou'll be notified when it's available!"

        sent = await query.edit_message_text(success_msg, parse_mode="Markdown")
        
        # Clean up all tracked messages from the flow, but NOT the confirmation message
        await cleanup_messages(context, query.message.chat_id, query.from_user.id)
        
        # Store confirmation message ID in watchlist so it can be deleted when available
        # We'll update the watchlist entry we just added
        if not is_available:  # Only store if we're actually waiting for availability
            from database import get_watchlist, update_watchlist
            watchlist = get_watchlist()
            for w in watchlist:
                if (w.get("media_id") == media_id and 
                    w.get("media_type") == media_type and
                    w.get("chat_id") == query.message.chat_id):
                    w["confirmation_message_id"] = sent.message_id
                    break
            update_watchlist(watchlist)
        else:
            # If already available, delete confirmation after 30 seconds
            await schedule_autodelete(context, query.message.chat_id, sent.message_id, 30)

    except Exception as e:
        logger.exception("confirm_handler error: %s", e)
        error_msg = f"❌ Error submitting request: {str(e)}"
        sent = await query.edit_message_text(error_msg)
        await schedule_autodelete(context, query.message.chat_id, sent.message_id, 10)


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button."""
    query = update.callback_query
    await query.answer("Request cancelled")

    try:
        await query.message.delete()
    except Exception:
        pass



async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline queries for search."""
    query = (update.inline_query.query or "").strip()
    user = update.inline_query.from_user
    logger.info("Inline query: %r from user=%s", query, user.id if user else "unknown")

    if not query:
        try:
            await update.inline_query.answer([], cache_time=0, is_personal=True)
        except Exception:
            pass
        return

    # Try Overseerr search
    try:
        movies = search_media(query, "movie") or []
    except Exception as e:
        logger.exception("search_media(movie) failed: %s", e)
        movies = []

    try:
        tvs = search_media(query, "tv") or []
    except Exception as e:
        logger.exception("search_media(tv) failed: %s", e)
        tvs = []

    items = (movies + tvs)[:8]

    # Fallback to TMDB if no results
    if not items and TMDB_API_KEY:
        try:
            items = tmdb_search(query, "movie", limit=8) or tmdb_search(query, "tv", limit=8) or []
        except Exception:
            items = []

    results = []
    for it in items:
        media_type = it.get("media_type") or ("movie" if it.get("releaseDate") or it.get("release_date") else "tv")
        media_id = it.get("id")
        title = it.get("title") or it.get("name") or "Unknown"
        year = safe_year(it.get("releaseDate") or it.get("release_date") or it.get("firstAirDate") or it.get("first_air_date"))
        label = f"{title}" + (f" ({year})" if year else "")
        input_text = f"/_inlineopen {media_type} {media_id}"

        poster_path = it.get("posterPath") or it.get("poster_path")
        thumb = None
        if poster_path:
            thumb = f"https://image.tmdb.org/t/p/w154{poster_path}"

        rid = str(uuid.uuid4())
        description = (it.get("overview") or "")[:120]

        if thumb:
            try:
                results.append(
                    InlineQueryResultPhoto(
                        id=rid,
                        photo_url=thumb,
                        thumb_url=thumb,
                        title=label,
                        description=description,
                        input_message_content=InputTextMessageContent(input_text),
                    )
                )
            except TypeError:
                results.append(
                    InlineQueryResultArticle(
                        id=rid,
                        title=label,
                        input_message_content=InputTextMessageContent(input_text),
                        description=description,
                    )
                )
        else:
            results.append(
                InlineQueryResultArticle(
                    id=rid,
                    title=label,
                    input_message_content=InputTextMessageContent(input_text),
                    description=description,
                )
            )

    try:
        await update.inline_query.answer(results, cache_time=60, is_personal=True)
    except Exception as e:
        logger.exception("Failed to answer inline query: %s", e)
        try:
            await update.inline_query.answer([], cache_time=0, is_personal=True)
        except Exception:
            pass


async def inlineopen_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline selection opens (/_inlineopen command)."""
    text = (update.message.text or "").strip()
    parts = text.split()
    if len(parts) < 3:
        return

    _, media_type, raw_id = parts[0], parts[1], parts[2]
    id_type = parts[3].lower() if len(parts) >= 4 else "overseerr"

    try:
        media_id = int(raw_id)
    except Exception:
        media_id = raw_id

    # TMDB explicit
    if id_type == "tmdb":
        details = None
        try:
            details = get_tmdb_details_or_none(media_id, media_type)
        except Exception as e:
            logger.exception("inlineopen_handler: TMDB fallback failed: %s", e)

        if details:
            await send_rich_poster(update.effective_chat.id, details, media_type, context, user_id=update.effective_user.id)
            return
        else:
            await update.message.reply_text("Could not find details on TMDB for that item.")
            return

    # Try Overseerr first
    try:
        details = get_details(media_id, media_type)
        if not details:
            raise Exception("get_details returned empty")
        await send_rich_poster(update.effective_chat.id, details, media_type, context, user_id=update.effective_user.id)
        return
    except requests.exceptions.HTTPError as he:
        logger.exception("inlineopen_handler: Overseerr HTTPError: %s", he)
    except Exception as e:
        logger.exception("inlineopen_handler: error calling get_details: %s", e)

    # Fallback to TMDB
    if TMDB_API_KEY:
        try:
            tmdb_details = get_tmdb_details_or_none(media_id, media_type)
            if tmdb_details:
                await send_rich_poster(update.effective_chat.id, tmdb_details, media_type, context, user_id=update.effective_user.id)
                return
        except Exception as e:
            logger.exception("inlineopen_handler: TMDB fallback also failed: %s", e)

    await update.message.reply_text(
        "Sorry - I couldn't fetch details from Overseerr for that item. "
        "You can try again, or use `/searchmovie` or `/searchtv` to find it."
    )


async def recommend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle recommendations button (recommend: callback)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("recommend:"):
        return

    parts = data.split(":", 2)
    if len(parts) < 3:
        return

    media_type, media_id = parts[1], parts[2]

    try:
        media_id = int(media_id)
    except Exception:
        pass

    try:
        recommendations = get_recommendations(media_id, media_type)
        if not recommendations:
            await query.edit_message_text("No recommendations found for this title.")
            return

        # Build inline buttons for recommendations
        kb = []
        lines = []
        for rec in recommendations[:5]:
            rec_id = rec.get("id")
            rec_title = rec.get("title") or rec.get("name") or "Unknown"
            rec_year = safe_year(rec.get("releaseDate") or rec.get("release_date") or rec.get("firstAirDate") or rec.get("first_air_date"))
            label = f"{rec_title}" + (f" ({rec_year})" if rec_year else "")
            kb.append([InlineKeyboardButton(label, callback_data=f"openrec:{media_type}:{rec_id}")])
            lines.append(label)

        await query.edit_message_text(
            "🔁 Recommendations:\n\n" + "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except Exception as e:
        logger.exception("recommend_handler error: %s", e)
        await query.edit_message_text("Error fetching recommendations. Please try again.")


async def openrec_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle opening a recommendation (openrec: callback)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("openrec:"):
        return

    parts = data.split(":", 2)
    if len(parts) < 3:
        return

    media_type, media_id = parts[1], parts[2]

    try:
        media_id = int(media_id)
    except Exception:
        pass

    try:
        details = get_details(media_id, media_type)
        if not details:
            await query.edit_message_text("Could not fetch details.")
            return

        await send_rich_poster(
            query.message.chat_id,
            details,
            media_type,
            context,
            user_id=query.from_user.id
        )
        await cleanup_messages(context, query.message.chat_id, query.from_user.id)

    except Exception as e:
        logger.exception("openrec_handler error: %s", e)
        await query.edit_message_text("Error fetching details. Please try again.")


def get_recommendations(media_id, media_type: str, limit: int = 10) -> list:
    """Get recommendations from Overseerr API."""
    try:
        url = f"{OVERSEERR_URL}/{media_type}/{media_id}/recommendations"
        headers = {"X-Api-Key": OVERSEERR_API_KEY}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])[:limit]
    except Exception as e:
        logger.exception("get_recommendations error: %s", e)
        return []


def tmdb_search(query: str, media_type: str, limit: int = 10) -> list:
    """Search TMDB directly as fallback."""
    if not TMDB_API_KEY:
        return []

    try:
        if media_type == "movie":
            url = f"https://api.themoviedb.org/3/search/movie"
        else:
            url = f"https://api.themoviedb.org/3/search/tv"

        params = {
            "api_key": TMDB_API_KEY,
            "query": query,
            "page": 1
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])[:limit]

        # Normalize structure
        for r in results:
            r["media_type"] = media_type
            if "poster_path" in r:
                r["posterPath"] = r["poster_path"]

        return results

    except Exception as e:
        logger.exception("tmdb_search error: %s", e)
        return []


