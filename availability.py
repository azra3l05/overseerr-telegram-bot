# availability.py
"""
Availability checking job and related functions.
Monitors requested media and notifies users when items become available.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from overseerr_api import get_canonical_status
from database import get_watchlist, update_watchlist

logger = logging.getLogger(__name__)


def should_check_item(item: Dict) -> bool:
    """
    Determine if an item should be checked based on exponential backoff.

    Check intervals:
    - < 24 hours old: every 15 minutes (always check)
    - 1-7 days old: every 1 hour
    - 7+ days old: every 6 hours

    Returns:
        True if item should be checked, False to skip
    """
    added_at_str = item.get("added_at")
    last_checked_str = item.get("last_checked_at")

    if not added_at_str:
        # No added_at timestamp, check it
        return True

    try:
        added_at = datetime.strptime(added_at_str, "%Y-%m-%d %H:%M:%S")
        age = datetime.now() - added_at

        # Determine check interval based on age
        if age < timedelta(days=1):
            # Less than 24 hours old: check every 15 minutes (always check)
            interval = timedelta(minutes=15)
        elif age < timedelta(days=7):
            # 1-7 days old: check every hour
            interval = timedelta(hours=1)
        else:
            # 7+ days old: check every 6 hours
            interval = timedelta(hours=6)

        # If never checked before, check it now
        if not last_checked_str:
            return True

        # Check if enough time has passed since last check
        last_checked = datetime.strptime(last_checked_str, "%Y-%m-%d %H:%M:%S")
        time_since_check = datetime.now() - last_checked

        should_check = time_since_check >= interval

        if not should_check:
            age_hours = age.total_seconds() / 3600
            next_check_in = (interval - time_since_check).total_seconds() / 60
            logger.debug(
                f"Skipping {item.get('title')} (age: {age_hours:.1f}h, "
                f"checked {time_since_check.total_seconds() / 60:.1f}m ago, "
                f"next check in {next_check_in:.1f}m)"
            )

        return should_check

    except Exception as e:
        logger.error(f"Error in should_check_item: {e}")
        # If error parsing dates, check the item anyway
        return True


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
    checked_count = 0
    skipped_count = 0

    for w in list(watchlist):
        try:
            media_id = w.get("media_id")
            media_type = w.get("media_type")  # "movie" | "tv"
            chat_id = w.get("chat_id")
            title = w.get("title") or w.get("name") or str(media_id)
            season = w.get("season")  # may be None for movies
            library_name = w.get("library_name", "media")
            last_known = w.get("last_known_status")

            # Check if this item should be checked (exponential backoff)
            if not should_check_item(w):
                skipped_count += 1
                remaining.append(w)
                continue

            # Update last_checked_at timestamp
            w["last_checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            checked_count += 1

            # Try Radarr/Sonarr API first for real-time checking
            is_partially_available = False
            is_fully_available = False
            episodes_available = 0
            total_episodes = 0

            try:
                from radarr_sonarr_api import get_radarr_sonarr_api
                api = get_radarr_sonarr_api()

                if api and media_type == "movie":
                    is_available, data = api.check_movie_availability(media_id)
                    is_fully_available = is_available
                    logger.info(f"[Radarr API] {media_type} {media_id} available={is_available}")
                elif api and media_type == "tv":
                    # Need to get tvdb_id from Overseerr
                    from overseerr_api import get_details
                    details = get_details(media_id, "tv")
                    tvdb_id = details.get("externalIds", {}).get("tvdbId")
                    if tvdb_id:
                        is_partially_available, is_fully_available, episodes_available, total_episodes, data = api.check_tv_availability(tvdb_id, season_number=season)
                        logger.info(f"[Sonarr API] {media_type} {media_id} season {season} partial={is_partially_available} full={is_fully_available} ({episodes_available}/{total_episodes})")
            except Exception as e:
                logger.warning(f"Radarr/Sonarr API check failed, falling back to Overseerr: {e}")
                # Fall back to Overseerr canonical status
                requested = [season] if (media_type == "tv" and season is not None) else None
                status, meta = get_canonical_status(media_id, media_type, requested_seasons=requested)
                is_fully_available = status == "AVAILABLE"
                is_partially_available = status == "PARTIALLY_AVAILABLE"
                logger.info(f"[Overseerr fallback] {media_type} {media_id} status={status}")

            # Handle availability states
            if is_fully_available:
                # Fully available - send notification and prune
                if last_known != "complete_notified":
                    try:
                        if media_type == "tv" and total_episodes > 0:
                            season_text = f" S{season}" if season else ""
                            message = f"✅ \"{title}\"{season_text} is now complete! All {total_episodes} episodes available in the {library_name} library."
                        else:
                            message = f"🎉 \"{title}\" is now available in the {library_name} library. Enjoy!"

                        await context.bot.send_message(chat_id, message)

                        # Delete the confirmation message now that content is available
                        confirmation_msg_id = w.get("confirmation_message_id")
                        if confirmation_msg_id:
                            try:
                                await context.bot.delete_message(chat_id, confirmation_msg_id)
                            except Exception as del_err:
                                logger.warning(f"Could not delete confirmation message {confirmation_msg_id}: {del_err}")
                    except Exception:
                        logger.exception("Failed to notify chat_id=%s for %s %s", chat_id, media_type, media_id)
                # Stop tracking this entry (prune from watchlist)
                continue

            elif is_partially_available and media_type == "tv":
                # Partially available - notify once, then keep tracking
                if last_known not in ("partially_notified", "complete_notified"):
                    try:
                        season_text = f" S{season}" if season else ""
                        message = f"📺 \"{title}\"{season_text}: {episodes_available} out of {total_episodes} episodes available in the {library_name} library. Start watching now!"
                        await context.bot.send_message(chat_id, message)
                        w["last_known_status"] = "partially_notified"
                    except Exception:
                        logger.exception("Failed to notify chat_id=%s for %s %s", chat_id, media_type, media_id)
                else:
                    # Already notified about partial, keep existing status
                    pass
                remaining.append(w)

            else:
                # Still not available → check if failed after 24h
                added_at_str = w.get("added_at")
                failed_notified = w.get("failed_notified", False)

                if added_at_str and not failed_notified:
                    try:
                        added_at = datetime.strptime(added_at_str, "%Y-%m-%d %H:%M:%S")
                        age = datetime.now() - added_at

                        # If request is >7 days old, check if it failed to find
                        if age > timedelta(days=7):
                            is_failed = False

                            # Check Radarr/Sonarr for monitoring status
                            try:
                                from radarr_sonarr_api import get_radarr_sonarr_api
                                api = get_radarr_sonarr_api()

                                if api and media_type == "movie":
                                    is_available, data = api.check_movie_availability(media_id)
                                    # If monitored but no files and no queue → failed
                                    if data and data.get("monitored") and not data.get("hasFile") and not data.get("queue"):
                                        is_failed = True
                                elif api and media_type == "tv":
                                    from overseerr_api import get_details
                                    details = get_details(media_id, "tv")
                                    tvdb_id = details.get("externalIds", {}).get("tvdbId")
                                    if tvdb_id:
                                        _, _, _, _, data = api.check_tv_availability(tvdb_id, season_number=season)
                                        # If monitored but no files → failed
                                        if data and data.get("monitored") and data.get("statistics", {}).get("episodeFileCount", 0) == 0:
                                            is_failed = True
                            except Exception as api_err:
                                logger.warning(f"Failed check API error: {api_err}")

                            # Send failed notification if content couldn't be found
                            if is_failed:
                                try:
                                    season_text = f" S{season}" if media_type == "tv" and season else ""
                                    message = (
                                        f"❌ Unable to find: {title}{season_text}\n"
                                        f"{'Radarr' if media_type == 'movie' else 'Sonarr'} couldn't locate this on any indexers after 7 days."
                                    )
                                    await context.bot.send_message(chat_id, message)
                                    w["failed_notified"] = True
                                    w["last_known_status"] = "failed"
                                    logger.info(f"Sent failed notification for {media_type} {media_id}")
                                except BadRequest as br:
                                    # Common case: "Chat not found" — user blocked the bot or deleted account.
                                    # Mark as notified so we don't keep retrying. Log at INFO, no traceback.
                                    w["failed_notified"] = True
                                    w["last_known_status"] = "failed"
                                    logger.info(f"Skipped failure notification for {media_type} {media_id} (chat_id={chat_id}): {br}")
                                except Exception as notif_err:
                                    logger.exception(f"Failed to send failure notification: {notif_err}")
                    except Exception as e:
                        logger.error(f"Error checking failed status: {e}")

                # Keep tracking
                w["last_known_status"] = w.get("last_known_status", "checking")
                remaining.append(w)

        except Exception:
            logger.exception("Availability check failed for entry: %r", w)
            # Keep it so we try again next run
            remaining.append(w)
    
    before = len(watchlist)
    after = len(remaining)
    pruned = before - after
    logger.info(
        f"Availability check: checked {checked_count}, skipped {skipped_count}, "
        f"pruned {pruned} item(s); {before} → {after} pending"
    )

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


async def weekly_pending_digest(context: ContextTypes.DEFAULT_TYPE):
    """
    Weekly digest job - sends summary of pending requests to group.
    No user names/tags, just general statistics.
    Runs every Sunday.
    """
    try:
        from database import get_all_requests
        from config import ALLOWED_CHAT_IDS

        watchlist = get_watchlist()
        all_requests = get_all_requests()

        if not all_requests:
            logger.info("No requests to report in weekly digest")
            return

        # Calculate statistics
        total_requests = len(all_requests)
        pending_count = len([w for w in watchlist if w.get("last_known_status") in ("checking", "partially_notified")])
        failed_count = len([w for w in watchlist if w.get("last_known_status") == "failed"])

        # Calculate fulfilled this week (requests where content became available in last 7 days)
        # This is an approximation - we count total requests minus pending
        fulfilled_estimate = max(0, total_requests - pending_count - failed_count)

        # Build message
        message_lines = [
            "📋 *Weekly Pending Requests Summary*\n",
            f"⏳ *{pending_count}* requests still searching for content",
            f"✅ Approximately *{fulfilled_estimate}* requests fulfilled",
        ]

        if failed_count > 0:
            message_lines.append(f"❌ *{failed_count}* requests couldn't be found on indexers")

        message_lines.append(f"\n📊 Total tracked requests: *{total_requests}*")
        message_lines.append("\nUse /myrequests to see your personal request history.")

        message = "\n".join(message_lines)

        # Send to group (use first allowed chat ID)
        chat_ids = ALLOWED_CHAT_IDS.split(",") if isinstance(ALLOWED_CHAT_IDS, str) else [ALLOWED_CHAT_IDS]
        for chat_id_str in chat_ids:
            try:
                chat_id = int(chat_id_str.strip())
                await context.bot.send_message(chat_id, message, parse_mode="Markdown")
                logger.info(f"Sent weekly pending digest to chat {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send weekly digest to {chat_id_str}: {e}")

    except Exception as e:
        logger.exception(f"Weekly pending digest failed: {e}")


async def daily_quality_check_job(context):
    """
    Daily job to check for quality upgrades.
    Runs at 3 AM to check if better quality versions are available.
    """
    from quality_upgrade_checker import check_quality_upgrades_job
    try:
        logger.info("Starting daily quality upgrade check...")
        await check_quality_upgrades_job(context)
    except Exception as e:
        logger.exception(f"Daily quality check failed: {e}")
