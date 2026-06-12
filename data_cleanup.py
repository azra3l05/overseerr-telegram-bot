# data_cleanup.py
"""
Data cleanup and retention management.
Removes old entries from watchlist and request logs based on configurable retention periods.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple
from database import get_watchlist, update_watchlist, _load_json, _save_json, _get_postgres_connection
from config import (
    REQUESTS_LOG_FILE,
    POSTGRES_SCHEMA,
    POSTGRES_ENABLED
)

logger = logging.getLogger(__name__)


def cleanup_watchlist(
    failed_retention_days: int = 30,
    checking_retention_days: int = 120
) -> Dict[str, int]:
    """
    Clean up old watchlist entries.

    Rules:
    - Remove "failed" entries older than failed_retention_days (default 30 days)
    - Remove "checking" entries older than checking_retention_days (default 120 days)
    - Keep "partially_notified" entries (still getting new episodes)
    - Keep recent entries regardless of status

    Args:
        failed_retention_days: Days to keep failed entries
        checking_retention_days: Days to keep checking entries

    Returns:
        Stats dict: {"removed_failed": N, "removed_checking": N, "kept": N}
    """
    watchlist = get_watchlist()

    if not watchlist:
        logger.info("Watchlist is empty, nothing to clean")
        return {"removed_failed": 0, "removed_checking": 0, "kept": 0}

    now = datetime.now()
    removed_failed = 0
    removed_checking = 0
    kept_items = []

    for item in watchlist:
        status = item.get("last_known_status")
        added_at_str = item.get("added_at")
        title = item.get("title", "Unknown")

        # Skip if no added_at timestamp
        if not added_at_str:
            kept_items.append(item)
            continue

        try:
            added_at = datetime.strptime(added_at_str, "%Y-%m-%d %H:%M:%S")
            age_days = (now - added_at).days

            # Remove old "failed" entries
            if status == "failed" and age_days > failed_retention_days:
                logger.info(f"Removing failed entry (age: {age_days}d): {title}")
                removed_failed += 1
                continue

            # Remove old "checking" entries (likely won't be found)
            if status == "checking" and age_days > checking_retention_days:
                logger.info(f"Removing stale checking entry (age: {age_days}d): {title}")
                removed_checking += 1
                continue

            # Keep everything else
            kept_items.append(item)

        except Exception as e:
            logger.error(f"Error processing watchlist item {title}: {e}")
            # Keep item if we can't process it
            kept_items.append(item)

    # Update watchlist
    if removed_failed > 0 or removed_checking > 0:
        update_watchlist(kept_items)
        logger.info(
            f"Watchlist cleanup: removed {removed_failed} failed, "
            f"{removed_checking} stale checking entries; kept {len(kept_items)}"
        )
    else:
        logger.info(f"Watchlist cleanup: nothing to remove, {len(kept_items)} entries kept")

    return {
        "removed_failed": removed_failed,
        "removed_checking": removed_checking,
        "kept": len(kept_items)
    }


def cleanup_request_log(retention_months: int = 12) -> Dict[str, int]:
    """
    Clean up old request log entries.

    Removes entries older than retention_months from both JSON and PostgreSQL.

    Args:
        retention_months: Number of months to keep (default 12)

    Returns:
        Stats dict: {"removed_json": N, "removed_postgres": N, "kept_json": N, "kept_postgres": N}
    """
    cutoff_date = datetime.now() - timedelta(days=retention_months * 30)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")

    stats = {
        "removed_json": 0,
        "removed_postgres": 0,
        "kept_json": 0,
        "kept_postgres": 0
    }

    # Cleanup JSON
    try:
        logs = _load_json(REQUESTS_LOG_FILE, [])
        original_count = len(logs)

        kept_logs = []
        for entry in logs:
            timestamp_str = entry.get("timestamp")
            if not timestamp_str:
                # Keep entries without timestamp
                kept_logs.append(entry)
                continue

            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                if timestamp >= cutoff_date:
                    kept_logs.append(entry)
                else:
                    stats["removed_json"] += 1
            except Exception as e:
                logger.error(f"Error parsing timestamp {timestamp_str}: {e}")
                # Keep entries we can't parse
                kept_logs.append(entry)

        stats["kept_json"] = len(kept_logs)

        if stats["removed_json"] > 0:
            _save_json(REQUESTS_LOG_FILE, kept_logs)
            logger.info(
                f"Request log JSON cleanup: removed {stats['removed_json']} old entries, "
                f"kept {stats['kept_json']}"
            )
        else:
            logger.info(f"Request log JSON cleanup: nothing to remove, {stats['kept_json']} entries kept")

    except Exception as e:
        logger.error(f"Failed to cleanup request log JSON: {e}")

    # Cleanup PostgreSQL
    if POSTGRES_ENABLED:
        conn = _get_postgres_connection()
        if conn:
            try:
                cursor = conn.cursor()

                # Get count before deletion
                cursor.execute(
                    f"SELECT COUNT(*) FROM {POSTGRES_SCHEMA}.telegram_requests WHERE requested_at < %s",
                    (cutoff_date,)
                )
                stats["removed_postgres"] = cursor.fetchone()[0]

                # Delete old entries
                cursor.execute(
                    f"DELETE FROM {POSTGRES_SCHEMA}.telegram_requests WHERE requested_at < %s",
                    (cutoff_date,)
                )

                # Get count after deletion
                cursor.execute(f"SELECT COUNT(*) FROM {POSTGRES_SCHEMA}.telegram_requests")
                stats["kept_postgres"] = cursor.fetchone()[0]

                conn.commit()
                cursor.close()

                if stats["removed_postgres"] > 0:
                    logger.info(
                        f"Request log PostgreSQL cleanup: removed {stats['removed_postgres']} old entries, "
                        f"kept {stats['kept_postgres']}"
                    )
                else:
                    logger.info(
                        f"Request log PostgreSQL cleanup: nothing to remove, "
                        f"{stats['kept_postgres']} entries kept"
                    )

            except Exception as e:
                logger.error(f"Failed to cleanup request log PostgreSQL: {e}")
                try:
                    conn.rollback()
                except:
                    pass

    return stats


def cleanup_all() -> Dict[str, any]:
    """
    Run all cleanup operations.

    Returns:
        Combined stats from all cleanup operations
    """
    logger.info("Running data cleanup operations...")

    results = {
        "watchlist": cleanup_watchlist(),
        "request_log": cleanup_request_log()
    }

    total_removed = (
        results["watchlist"]["removed_failed"] +
        results["watchlist"]["removed_checking"] +
        results["request_log"]["removed_json"] +
        results["request_log"]["removed_postgres"]
    )

    logger.info(f"Data cleanup complete: removed {total_removed} total entries")

    return results


async def scheduled_cleanup(context):
    """Job function for scheduled cleanup (called by job scheduler)."""
    logger.info("Running scheduled data cleanup...")

    try:
        results = cleanup_all()

        # Log summary
        watchlist_removed = results["watchlist"]["removed_failed"] + results["watchlist"]["removed_checking"]
        request_log_removed = results["request_log"]["removed_json"] + results["request_log"]["removed_postgres"]

        logger.info(
            f"Scheduled cleanup completed: "
            f"watchlist={watchlist_removed}, request_log={request_log_removed}"
        )

    except Exception as e:
        logger.exception(f"Scheduled cleanup failed: {e}")
