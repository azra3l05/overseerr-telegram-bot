# handlers/callbacks.py
"""
Callback handlers for inline buttons and queries.
Handles user interactions with buttons, library selection, confirmations, etc.
"""
import logging
import uuid
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import InlineQueryResultArticle, InputTextMessageContent, InlineQueryResultPhoto
from telegram.ext import ContextTypes
import requests

from overseerr_api import search_media, get_details, request_media
from config import LIBRARIES_MOVIES, LIBRARIES_TV, TMDB_API_KEY, OVERSEERR_API_URL, OVERSEERR_API_KEY, RADARR_API_URL, RADARR_API_KEY, SONARR_API_URL, SONARR_API_KEY
from tag_manager import get_bot_and_user_tags
from database import (
    log_request, add_to_watchlist, can_add_to_watchlist,
    get_watchlist, update_watchlist
)
from utils import (
    safe_year, title_with_year_from_details, is_available,
    imdb_url_from_details, get_tmdb_details_or_none, tmdb_search
)
from handlers.utils import escape_markdown
from rate_limiter import rate_limit
from handlers.utils import (
    track_message, cleanup_messages, schedule_autodelete, send_rich_poster,
    _cmd_log, _event_log
)

# Aliases for backward compatibility
MOVIE_LIBRARIES = LIBRARIES_MOVIES
TV_LIBRARIES = LIBRARIES_TV

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def check_duplicate_season_request(tmdb_id: int, requested_season: int) -> Optional[dict]:
    """
    Check if another season of the same show is already pending.
    Returns info about the duplicate request if found, None otherwise.
    """
    try:
        from config import POSTGRES_ENABLED, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD

        if not POSTGRES_ENABLED:
            return None

        import psycopg2

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DATABASE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        conn.autocommit = True  # prevent stale idle-in-transaction
        cursor = conn.cursor()

        # Check for existing pending requests with same tmdb_id but different season
        cursor.execute("""
            SELECT season, user_name, request_status
            FROM public.telegram_requests
            WHERE tmdb_id = %s
              AND media_type = 'tv'
              AND season IS NOT NULL
              AND season != %s
              AND request_status IN ('PENDING', 'PROCESSING', 'PARTIALLY_AVAILABLE')
            ORDER BY requested_at DESC
            LIMIT 1
        """, (tmdb_id, requested_season))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result:
            return {
                'season': result[0],
                'user': result[1],
                'status': result[2]
            }

        return None

    except Exception as e:
        logger.exception(f"Error checking duplicate season request: {e}")
        return None


def log_rejected_request(
    telegram_user_id: int,
    telegram_user: str,
    media_title: str,
    media_type: str,
    season: int,
    library_name: str,
    tmdb_id: int,
    rejection_reason: str,
    chat_id: int = None
) -> bool:
    """
    Log a rejected request to the database.
    """
    try:
        from config import POSTGRES_ENABLED, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD
        from datetime import datetime

        if not POSTGRES_ENABLED:
            return False

        import psycopg2

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DATABASE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        conn.autocommit = True  # prevent stale idle-in-transaction
        cursor = conn.cursor()

        # Insert rejected request
        cursor.execute("""
            INSERT INTO public.telegram_requests
            (user_name, title, media_type, season, library_name, tmdb_id,
             telegram_user_id, chat_id, rejected, rejection_reason, rejected_at, rejected_by,
             request_status, requested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, NOW(), 'AUTO', 'REJECTED', NOW())
        """, (telegram_user, media_title, media_type, season, library_name, tmdb_id,
              telegram_user_id, chat_id, rejection_reason))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"Logged rejected request: {media_title} (Season {season}) by {telegram_user}")
        return True

    except Exception as e:
        logger.exception(f"Error logging rejected request: {e}")
        return False


# ---------------------------------------------------------------------------
# Media Selection Handlers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Library Selection Handlers
# ---------------------------------------------------------------------------

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

    # Add cancel button
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

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

    _event_log("library_selected", update,
               media_type=media_type, media_id=media_id, library_id=library_id)

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

            # Add cancel button
            kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

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

            # Get poster URL
            poster_path = details.get("posterPath")
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

            caption = f"*Confirm request:*\n\n*{escape_markdown(title)}*\nLibrary: {escape_markdown(lib_name)}"

            # Delete current message and send new one with poster
            try:
                await query.message.delete()
            except Exception:
                pass

            if poster_url:
                try:
                    sent = await context.bot.send_photo(
                        query.message.chat_id,
                        photo=poster_url,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                    track_message(query.from_user.id, sent.message_id)
                except Exception:
                    # Fallback to text if poster fails
                    sent = await context.bot.send_message(
                        query.message.chat_id,
                        caption,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                    track_message(query.from_user.id, sent.message_id)
            else:
                sent = await context.bot.send_message(
                    query.message.chat_id,
                    caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                track_message(query.from_user.id, sent.message_id)

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

    _event_log("season_selected", update,
               media_id=media_id, library_id=library_id, season=season)

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

        # Get poster URL
        poster_path = details.get("posterPath")
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        caption = f"*Confirm request:*\n\n*{escape_markdown(title)}*\nSeason: {season}\nLibrary: {escape_markdown(lib_name)}"

        # Delete current message and send new one with poster
        try:
            await query.message.delete()
        except Exception:
            pass

        if poster_url:
            try:
                sent = await context.bot.send_photo(
                    query.message.chat_id,
                    photo=poster_url,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                track_message(query.from_user.id, sent.message_id)
            except Exception:
                # Fallback to text if poster fails
                sent = await context.bot.send_message(
                    query.message.chat_id,
                    caption,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                track_message(query.from_user.id, sent.message_id)
        else:
            sent = await context.bot.send_message(
                query.message.chat_id,
                caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            track_message(query.from_user.id, sent.message_id)

    except Exception as e:
        logger.exception("season_handler error: %s", e)
        await query.edit_message_text("Error processing your request. Please try again.")


# ---------------------------------------------------------------------------
# Request Confirmation Handlers
# ---------------------------------------------------------------------------

@rate_limit('request', max_calls=5, window_seconds=60)
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
        # Check for duplicate season requests (TV shows only)
        if media_type == "tv" and season is not None:
            duplicate = check_duplicate_season_request(media_id, season)
            if duplicate:
                # Auto-reject this request
                details = get_details(media_id, media_type)
                title = title_with_year_from_details(details, media_type)

                # Find library name
                lib_name = next((name for name, lid in TV_LIBRARIES.items() if lid == library_id), "Unknown")

                _event_log("request_blocked_duplicate_season", update,
                           title=title, tmdb_id=media_id, season=season,
                           duplicate_season=duplicate.get("season"),
                           duplicate_user=duplicate.get("user"))

                # Log as rejected request
                log_rejected_request(
                    telegram_user_id=query.from_user.id,
                    telegram_user=telegram_user,
                    media_title=title,
                    media_type=media_type,
                    season=season,
                    library_name=lib_name,
                    tmdb_id=media_id,
                    rejection_reason=f"Another season (Season {duplicate['season']}) is already pending by {duplicate['user']}",
                    chat_id=query.message.chat_id
                )

                # Send rejection message
                rejection_msg = (
                    f"❌ *Request Not Allowed*\n\n"
                    f"📺 *{escape_markdown(title)}* - Season {season}\n\n"
                    f"Another season (*Season {duplicate['season']}*) of this show is already pending.\n"
                    f"Requested by: {escape_markdown(duplicate['user'])}\n"
                    f"Status: {duplicate['status']}\n\n"
                    f"⏳ Please wait for that to complete before requesting other seasons."
                )

                # Delete the photo message and send new rejection message
                try:
                    await query.message.delete()
                except Exception:
                    pass

                sent = await context.bot.send_message(
                    query.message.chat_id,
                    rejection_msg,
                    parse_mode="Markdown"
                )
                await schedule_autodelete(context, query.message.chat_id, sent.message_id, 30)
                return

        # Get tags for request
        tags = None
        try:
            # Determine service
            if media_type == "movie":
                service_url = RADARR_API_URL
                service_key = RADARR_API_KEY
                service_type = "radarr"
            else:
                service_url = SONARR_API_URL
                service_key = SONARR_API_KEY
                service_type = "sonarr"

            # Get both bot-type tag and user-specific tag
            tags = get_bot_and_user_tags(
                bot_type="telegram",
                username=telegram_user,
                service_url=service_url,
                api_key=service_key,
                service_type=service_type
            )
            logger.info(f"Requesting with tags: {tags}")
        except Exception as tag_err:
            logger.warning(f"Could not get tags: {tag_err}, proceeding without user tag")

        # Submit request to Overseerr with tags
        _event_log("request_submitted_to_seerr", update,
                   media_type=media_type, media_id=media_id,
                   library_id=library_id, season=season, tags=tags)
        if media_type == "movie":
            response = request_media(media_id, "movie", library_id=library_id, tags=tags)
        else:
            response = request_media(media_id, "tv", seasons=[season], library_id=library_id, tags=tags)

        # Resolve title up-front so we can use it in failure messages too
        details = get_details(media_id, media_type)
        title = title_with_year_from_details(details, media_type)

        # Validate Seerr response — guard against NULL overseerr_request_id.
        # Pre-2026-05-23: silent NULL writes produced "stuck" PENDING rows
        # that could never be tracked. See learnings/seerr-null-request-id-guard.md.
        if not isinstance(response, dict) or not response.get("id"):
            msg = response.get("message", "") if isinstance(response, dict) else ""
            logger.error(f"Seerr did not return a request_id for {media_id} ({media_type}): {response}")
            try:
                await query.message.delete()
            except Exception:
                pass
            is_already_available = any(t in msg.lower() for t in ("no seasons available", "already"))
            if is_already_available:
                _event_log("request_already_available", update,
                           title=title, tmdb_id=media_id, media_type=media_type,
                           message=msg)
                user_msg = f"ℹ️ **{title}** is already available — no new request needed."
            else:
                _event_log("request_failed_no_id", update,
                           title=title, tmdb_id=media_id, media_type=media_type,
                           message=msg)
                user_msg = f"❌ Request failed: {msg or 'Seerr returned no request ID'}"
                # Record FAILED attempt in PostgreSQL so it appears in /myrequests.
                # archived=TRUE (set by log_request when status='FAILED') means sync ignores it.
                lib_name_for_log = (
                    next((name for name, lid in MOVIE_LIBRARIES.items() if lid == library_id), "Unknown")
                    if media_type == "movie"
                    else next((name for name, lid in TV_LIBRARIES.items() if lid == library_id), "Unknown")
                )
                log_request(
                    telegram_user=telegram_user,
                    media_title=title,
                    media_type=media_type,
                    season=season,
                    library_name=lib_name_for_log,
                    tmdb_id=media_id,
                    overseerr_request_id=None,
                    telegram_user_id=query.from_user.id,
                    chat_id=query.message.chat_id,
                    status="FAILED",
                    failure_reason=msg or "Seerr returned no request ID",
                )
            await context.bot.send_message(
                query.message.chat_id,
                user_msg,
                parse_mode="Markdown"
            )
            return

        # Extract Overseerr request ID from response
        overseerr_request_id = response["id"]

        # Find library name - libraries are {name: id}
        if media_type == "movie":
            lib_name = next((name for name, lid in MOVIE_LIBRARIES.items() if lid == library_id), "Unknown")
        else:
            lib_name = next((name for name, lid in TV_LIBRARIES.items() if lid == library_id), "Unknown")

        _event_log("request_succeeded", update,
                   title=title, tmdb_id=media_id, media_type=media_type,
                   library=lib_name, season=season,
                   overseerr_request_id=overseerr_request_id)

        # Check request size limits before adding to watchlist - per user, not per chat
        can_add, limit_message = can_add_to_watchlist(query.from_user.id, media_type)
        if not can_add:
            _event_log("request_blocked_by_watchlist_limit", update,
                       title=title, tmdb_id=media_id, media_type=media_type,
                       reason=str(limit_message)[:80])
            # Delete the photo message and send limit message
            try:
                await query.message.delete()
            except Exception:
                pass

            await context.bot.send_message(
                query.message.chat_id,
                limit_message,
                parse_mode="Markdown"
            )
            return

        # Log to database
        log_request(
            telegram_user=telegram_user,
            media_title=title,
            media_type=media_type,
            season=season,
            library_name=lib_name,
            tmdb_id=media_id,
            overseerr_request_id=overseerr_request_id,
            telegram_user_id=query.from_user.id,
            chat_id=query.message.chat_id
        )

        # Add to watchlist for availability checking
        watchlist_success, watchlist_msg = add_to_watchlist(
            media_id=media_id,
            media_type=media_type,
            chat_id=query.message.chat_id,
            title=title,
            library_name=lib_name,
            season=season,
            check_limits=False,  # Already checked above
            telegram_user_id=query.from_user.id  # Track per-user limits
        )

        if not watchlist_success:
            logger.warning(f"Failed to add to watchlist: {watchlist_msg}")

        # Check immediate availability from Postgres
        is_available = False
        try:
            from postgres_checker import get_postgres_checker
            pg_checker = get_postgres_checker()
            logger.info(f"Postgres checker status: {pg_checker is not None}")

            if pg_checker:
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
                success_msg = f"✅ Successfully requested: *{escape_markdown(title)}*"
                if media_type == "tv" and season:
                    success_msg += f" (Season {season})"
                success_msg += f"\n\nYou'll be notified when it's available!"
        except Exception as e:
            logger.exception(f"Could not check immediate availability: {e}")
            success_msg = f"✅ Successfully requested: *{escape_markdown(title)}*"
            if media_type == "tv" and season:
                success_msg += f" (Season {season})"
            success_msg += f"\n\nYou'll be notified when it's available!"

        # Delete the photo message and send new text message
        try:
            await query.message.delete()
        except Exception:
            pass

        sent = await context.bot.send_message(
            query.message.chat_id,
            success_msg,
            parse_mode="Markdown"
        )

        # Clean up all tracked messages from the flow, but NOT the confirmation message
        await cleanup_messages(context, query.message.chat_id, query.from_user.id)

        # Store confirmation message ID in watchlist so it can be deleted when available
        # We'll update the watchlist entry we just added
        if not is_available:  # Only store if we're actually waiting for availability
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

        # Delete the photo message and send new error message
        try:
            await query.message.delete()
        except Exception:
            pass

        sent = await context.bot.send_message(
            query.message.chat_id,
            error_msg
        )
        await schedule_autodelete(context, query.message.chat_id, sent.message_id, 10)


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button."""
    query = update.callback_query
    await query.answer("Request cancelled")
    _event_log("request_cancelled", update)

    try:
        await query.message.delete()
    except Exception:
        pass


async def dismiss_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dismiss button on notifications."""
    query = update.callback_query
    await query.answer("✅ Notification dismissed")

    try:
        await query.message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Inline Query Handlers
# ---------------------------------------------------------------------------

@rate_limit('search', max_calls=10, window_seconds=60)
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


# ---------------------------------------------------------------------------
# Recommendation Handlers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def get_recommendations(media_id, media_type: str, limit: int = 10) -> list:
    """Get recommendations from Overseerr API."""
    try:
        url = f"{OVERSEERR_API_URL}/{media_type}/{media_id}/recommendations"
        headers = {"X-Api-Key": OVERSEERR_API_KEY}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])[:limit]
    except Exception as e:
        logger.exception("get_recommendations error: %s", e)
        return []
