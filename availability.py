# availability.py
"""
Availability checking job and related functions.
Monitors requested media and notifies users when items become available.
"""
import logging
from typing import Dict, Any, Optional
from telegram.ext import ContextTypes
from overseerr_api import get_canonical_status
from database import get_watchlist, update_watchlist

logger = logging.getLogger(__name__)


async def check_availability_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic job that checks watchlist for availability.
    - Reads watchlist from database
    - Checks each entry's availability via Radarr/Sonarr APIs (real-time)
    - Notifies on transition to AVAILABLE/PARTIALLY_AVAILABLE
    - Prunes available entries from watchlist
    """
    watchlist = get_watchlist()
    
    if not watchlist:
        logger.info("Watchlist is empty. Skipping check.")
        return

    remaining = []

    for w in list(watchlist):
        try:
            media_id = w.get("media_id")
            media_type = w.get("media_type")  # "movie" | "tv"
            chat_id = w.get("chat_id")
            title = w.get("title") or w.get("name") or str(media_id)
            season = w.get("season")  # may be None for movies
            library_name = w.get("library_name", "media")
            last_known = w.get("last_known_status")

            # Try Radarr/Sonarr API first for real-time checking
            is_now_available = False
            try:
                from radarr_sonarr_api import get_radarr_sonarr_api
                api = get_radarr_sonarr_api()
                
                if api and media_type == "movie":
                    is_available, data = api.check_movie_availability(media_id)
                    is_now_available = is_available
                    logger.info(f"[Radarr API] {media_type} {media_id} available={is_available}")
                elif api and media_type == "tv":
                    # Need to get tvdb_id from Overseerr
                    from overseerr_api import get_details
                    details = get_details(media_id, "tv")
                    tvdb_id = details.get("externalIds", {}).get("tvdbId")
                    if tvdb_id:
                        is_available, data = api.check_tv_availability(tvdb_id, season_number=season)
                        is_now_available = is_available
                        logger.info(f"[Sonarr API] {media_type} {media_id} season {season} available={is_available}")
            except Exception as e:
                logger.warning(f"Radarr/Sonarr API check failed, falling back to Overseerr: {e}")
                # Fall back to Overseerr canonical status
                requested = [season] if (media_type == "tv" and season is not None) else None
                status, meta = get_canonical_status(media_id, media_type, requested_seasons=requested)
                is_now_available = status in ("AVAILABLE", "PARTIALLY_AVAILABLE")
                logger.info(f"[Overseerr fallback] {media_type} {media_id} status={status}")

            # Notify on first transition, then prune
            if is_now_available:
                if last_known not in ("AVAILABLE", "PARTIALLY_AVAILABLE", "notified"):
                    try:
                        await context.bot.send_message(
                            chat_id,
                            f"🎉 \"{title}\" is now available in the {library_name} library. Enjoy!"
                        )
                        # Delete the confirmation message now that content is available
                        confirmation_msg_id = w.get("confirmation_message_id")
                        if confirmation_msg_id:
                            try:
                                await context.bot.delete_message(chat_id, confirmation_msg_id)
                            except Exception as del_err:
                                logger.warning(f"Could not delete confirmation message {confirmation_msg_id}: {del_err}")
                        # Mark as notified so we don't notify again if check runs before pruning
                        w["last_known_status"] = "notified"
                    except Exception:
                        logger.exception("Failed to notify chat_id=%s for %s %s", chat_id, media_type, media_id)
                # Stop tracking this entry
                continue

            # Still not available → keep tracking
            w["last_known_status"] = "checking"
            remaining.append(w)

        except Exception:
            logger.exception("Availability check failed for entry: %r", w)
            # Keep it so we try again next run
            remaining.append(w)
    
    before = len(watchlist)
    after = len(remaining)
    logger.info("Availability check: pruned %d item(s); %d → %d pending", before - after, before, after)

    # Update watchlist in database
    update_watchlist(remaining)


async def checknow_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger for availability check."""
    await update.message.reply_text("🔎 Checking availability now…")
    try:
        await check_availability_job(context)
        await update.message.reply_text("✅ Done checking availability.")
    except Exception as e:
        logger.exception("checknow failed")
        await update.message.reply_text(f"Oops — the check failed: {e}")
