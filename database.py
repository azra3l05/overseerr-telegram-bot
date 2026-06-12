# database.py
"""
Centralized data persistence layer with Postgres and JSON support.
Writes to both Postgres (primary) and JSON (backup) for request logs.
"""
import json
import os
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from config import (
    REQUESTS_LOG_FILE, AVAILABILITY_WATCH_FILE,
    POSTGRES_ENABLED, POSTGRES_HOST, POSTGRES_PORT,
    POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_SCHEMA
)

logger = logging.getLogger(__name__)

# Postgres connection (lazy loaded)
_pg_conn = None
_pg_enabled = POSTGRES_ENABLED


def _get_postgres_connection():
    """Get or create Postgres connection."""
    global _pg_conn, _pg_enabled
    
    if not _pg_enabled:
        return None
    
    if _pg_conn is None or _pg_conn.closed:
        try:
            import psycopg2
            _pg_conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                database=POSTGRES_DATABASE,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                connect_timeout=5
            )
            # Autocommit prevents stale "idle in transaction" connections —
            # every statement commits immediately, no implicit transaction
            # can leak across function boundaries. See learnings/
            # postgres-stale-transaction-blocks-alter.md for the bug this fixes.
            _pg_conn.autocommit = True
            logger.info("Postgres connection established for request logging (autocommit=True)")
        except Exception as e:
            logger.error(f"Failed to connect to Postgres: {e}")
            # Reset to None so subsequent calls retry — don't permanently
            # disable PG for the process lifetime on a single failure.
            _pg_conn = None
            return None

    return _pg_conn


def _load_json(path: str, default: Any) -> Any:
    """Load JSON file with error handling."""
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return default


def _save_json(path: str, data: Any) -> bool:
    """Save data to JSON file with error handling."""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        # Write to temporary file first, then rename (atomic operation)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # Atomic rename
        os.replace(temp_path, path)
        return True
    except Exception as e:
        logger.exception(f"Failed to save {path}: {e}")
        return False


# ============================================================================
# User Tracking
# ============================================================================

def track_user(
    telegram_user_id: int,
    telegram_username: str = None,
    telegram_first_name: str = None,
    telegram_last_name: str = None
) -> bool:
    """
    Track user information in the user mapping table.
    Updates last_seen and increments request_count on every call.
    Creates new entry if user doesn't exist.
    """
    conn = _get_postgres_connection()
    if not conn:
        logger.debug("Postgres not available, skipping user tracking")
        return False

    try:
        cursor = conn.cursor()

        # Build display name
        display_name = telegram_first_name or telegram_username or f"User{telegram_user_id}"
        if telegram_first_name and telegram_last_name:
            display_name = f"{telegram_first_name} {telegram_last_name}"

        # Upsert user info
        cursor.execute(
            f"""
            INSERT INTO {POSTGRES_SCHEMA}.telegram_user_mapping
            (telegram_user_id, telegram_username, telegram_first_name, telegram_last_name,
             telegram_display_name, first_seen, last_seen, request_count, last_request_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), 1, NOW())
            ON CONFLICT (telegram_user_id)
            DO UPDATE SET
                telegram_username = EXCLUDED.telegram_username,
                telegram_first_name = EXCLUDED.telegram_first_name,
                telegram_last_name = EXCLUDED.telegram_last_name,
                telegram_display_name = EXCLUDED.telegram_display_name,
                last_seen = NOW(),
                request_count = {POSTGRES_SCHEMA}.telegram_user_mapping.request_count + 1,
                last_request_at = NOW()
            """,
            (telegram_user_id, telegram_username, telegram_first_name, telegram_last_name, display_name)
        )
        conn.commit()
        cursor.close()
        logger.debug(f"Tracked user: {display_name} (ID: {telegram_user_id})")
        return True
    except Exception as e:
        logger.error(f"Failed to track user: {e}")
        try:
            conn.rollback()
        except:
            pass
        return False


# ============================================================================
# Request Logging
# ============================================================================

def log_request(
    telegram_user: str,
    media_title: str,
    media_type: str,
    season: int = None,
    library_name: str = None,
    tmdb_id: int = None,
    overseerr_request_id: int = None,
    telegram_user_id: int = None,
    telegram_first_name: str = None,
    telegram_last_name: str = None,
    priority: str = "normal",
    chat_id: int = None,
    status: str = "PENDING",
    failure_reason: str = None,
) -> bool:
    """
    Save request info with timestamp to both Postgres and JSON.
    Postgres is primary, JSON is backup.

    Args:
        priority: Request priority ("high", "normal", "low"). Default: "normal"
        status: 'PENDING' (default) for normal requests, 'FAILED' for attempts
                that didn't make it to Seerr. FAILED rows are archived immediately
                so the sync script ignores them but they remain visible in
                get_user_requests() for history.
        failure_reason: Optional human-readable reason; stored in rejection_reason
                column for FAILED rows.
    """
    # Track user information (if user ID provided)
    if telegram_user_id:
        track_user(telegram_user_id, telegram_user, telegram_first_name, telegram_last_name)

    timestamp = datetime.now()
    entry = {
        "user": telegram_user,
        "title": media_title,
        "type": media_type,
        "season": season,
        "library": library_name,
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "overseerr_request_id": overseerr_request_id,
        "tmdb_id": tmdb_id,
        "priority": priority,
    }
    
    # Try Postgres first
    pg_success = False
    conn = _get_postgres_connection()
    if conn:
        try:
            cursor = conn.cursor()

            # Explicit duplicate check (gives us the existing row id for clean logging).
            # The partial unique index idx_telegram_requests_active_uniq + ON CONFLICT
            # below is the race-safe safety net.
            cursor.execute(
                f"""
                SELECT id, requested_at, request_status
                FROM {POSTGRES_SCHEMA}.telegram_requests
                WHERE archived = FALSE
                  AND user_name = %s
                  AND COALESCE(tmdb_id, -1) = COALESCE(%s, -1)
                  AND media_type = %s
                  AND COALESCE(season, -1) = COALESCE(%s, -1)
                LIMIT 1
                """,
                (telegram_user, tmdb_id, media_type, season)
            )
            existing = cursor.fetchone()

            if existing:
                existing_id, existing_date, existing_status = existing
                logger.info(
                    f"[PG] Duplicate request skipped: {media_title} by {telegram_user} "
                    f"already exists as id={existing_id} "
                    f"(status={existing_status}, first requested {existing_date})"
                )
                pg_success = True  # request IS tracked, original row stands
            else:
                # FAILED rows are archived immediately — they're a historical record,
                # not something the sync script should poll. Reason goes in rejection_reason.
                is_failed = (status == "FAILED")
                archived_value = is_failed
                reason_value = failure_reason if is_failed else None

                cursor.execute(
                    f"""
                    INSERT INTO {POSTGRES_SCHEMA}.telegram_requests
                    (user_name, title, media_type, season, library_name, requested_at, tmdb_id,
                     overseerr_request_id, priority, request_status, telegram_user_id, chat_id,
                     archived, rejection_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (telegram_user, media_title, media_type, season, library_name, timestamp, tmdb_id,
                     overseerr_request_id, priority, status, telegram_user_id, chat_id,
                     archived_value, reason_value)
                )
                inserted = cursor.fetchone()
                conn.commit()
                pg_success = True
                if inserted:
                    logger.info(f"[PG] Logged request ({status}): {media_title} by {telegram_user} (id={inserted[0]})")
                else:
                    # Race condition: another concurrent insert beat us; unique index caught it
                    logger.info(f"[PG] Concurrent duplicate suppressed by unique index: {media_title} by {telegram_user}")

            cursor.close()
        except Exception as e:
            logger.error(f"Failed to log request to Postgres: {e}")
            try:
                conn.rollback()
            except:
                pass
    
    # Always write to JSON as backup
    logs = _load_json(REQUESTS_LOG_FILE, [])
    logs.append(entry)
    json_success = _save_json(REQUESTS_LOG_FILE, logs)
    
    if json_success and not pg_success:
        logger.warning(f"[JSON] Request logged to JSON only (Postgres unavailable)")
    
    return pg_success or json_success


def get_user_requests(telegram_user: str, limit: int = 10) -> List[Dict]:
    """Get requests for a specific user (tries Postgres first, falls back to JSON)."""
    conn = _get_postgres_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, user_name, title, media_type, season, library_name,
                       TO_CHAR(requested_at, 'YYYY-MM-DD HH24:MI:SS') as timestamp,
                       tmdb_id, COALESCE(priority, 'normal') as priority,
                       COALESCE(request_status, 'PENDING') as status
                FROM {POSTGRES_SCHEMA}.telegram_requests
                WHERE user_name = %s
                ORDER BY
                    CASE COALESCE(priority, 'normal')
                        WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 2
                    END,
                    requested_at DESC
                LIMIT %s
                """,
                (telegram_user, limit)
            )
            rows = cursor.fetchall()
            cursor.close()

            # Convert to dict format matching JSON
            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "user": row[1],
                    "title": row[2],
                    "type": row[3],
                    "season": row[4],
                    "library": row[5],
                    "timestamp": row[6],
                    "tmdb_id": row[7],
                    # overseerr_request_id removed
                    "priority": row[8],
                    "status": row[9]
                })
            
            logger.info(f"[PG] Retrieved {len(results)} requests for {telegram_user}, first result: {results[0] if results else 'none'}")
            return results
        except Exception as e:
            logger.exception(f"Failed to get user requests from Postgres: {e}")
    
    # Fallback to JSON
    logs = _load_json(REQUESTS_LOG_FILE, [])
    user_requests = [r for r in logs if r["user"] == telegram_user]
    return user_requests[-limit:]


def get_all_requests(limit: int = None) -> List[Dict]:
    """Get all requests (tries Postgres first, falls back to JSON)."""
    conn = _get_postgres_connection()
    if conn:
        try:
            cursor = conn.cursor()
            sql = f"""
                SELECT id, user_name, title, media_type, season, library_name,
                       TO_CHAR(requested_at, 'YYYY-MM-DD HH24:MI:SS') as timestamp,
                       tmdb_id, COALESCE(priority, 'normal') as priority,
                       COALESCE(request_status, 'PENDING') as status
                FROM {POSTGRES_SCHEMA}.telegram_requests
                ORDER BY requested_at DESC
            """
            if limit:
                sql += f" LIMIT {limit}"

            cursor.execute(sql)
            rows = cursor.fetchall()
            cursor.close()

            # Convert to dict format
            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "user": row[1],
                    "title": row[2],
                    "type": row[3],
                    "season": row[4],
                    "library": row[5],
                    "timestamp": row[6],
                    "tmdb_id": row[7],
                    "priority": row[8],
                    "status": row[9]
                })

            logger.debug(f"[PG] Retrieved {len(results)} total requests")
            return results
        except Exception as e:
            logger.error(f"Failed to get all requests from Postgres: {e}")

    # Fallback to JSON
    logs = _load_json(REQUESTS_LOG_FILE, [])
    return logs[-limit:] if limit else logs


# ============================================================================
# Availability Watchlist
# ============================================================================

def add_to_watchlist(
    media_id: int,
    media_type: str,
    chat_id: int,
    title: str,
    library_name: str = None,
    season: int = None,
    check_limits: bool = True,
    track_quality_upgrade: bool = False,
    telegram_user_id: int = None
) -> tuple[bool, str]:
    """
    Add an item to the availability watchlist.

    Args:
        check_limits: If True, check request size limits before adding
        track_quality_upgrade: If True, monitor for quality upgrades after availability
        telegram_user_id: Telegram user ID (for per-user limits in group chats)

    Returns:
        (success: bool, message: str)
    """
    watchlist = _load_json(AVAILABILITY_WATCH_FILE, [])

    # Check if already exists
    exists = any(
        (w.get("media_id") == media_id and
         w.get("media_type") == media_type and
         w.get("season") == season)
        for w in watchlist
    )

    if exists:
        logger.debug(f"Item already in watchlist: {title}")
        return True, "Already in watchlist"

    # Check request size limits - use telegram_user_id if provided, otherwise fall back to chat_id
    if check_limits:
        user_id_for_limits = telegram_user_id if telegram_user_id else chat_id
        can_add, limit_message = can_add_to_watchlist(user_id_for_limits, media_type)
        if not can_add:
            logger.info(f"User {user_id_for_limits} hit request limit for {media_type}")
            return False, limit_message

    entry = {
        "media_id": media_id,
        "media_type": media_type,
        "season": season,
        "chat_id": chat_id,
        "telegram_user_id": telegram_user_id,  # Store user ID for per-user tracking
        "title": title,
        "library_name": library_name,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_known_status": None,
        "track_quality_upgrade": track_quality_upgrade,
        "quality_score": 0,
        "upgrade_notified": False,
    }

    watchlist.append(entry)
    success = _save_json(AVAILABILITY_WATCH_FILE, watchlist)

    if success:
        logger.info(f"Added to watchlist: {title} (user: {telegram_user_id or chat_id})")
        return True, "Added to watchlist"
    else:
        logger.error(f"Failed to add to watchlist: {title}")
        return False, "Failed to save to watchlist"


def get_watchlist() -> List[Dict]:
    """Get all items in the watchlist."""
    return _load_json(AVAILABILITY_WATCH_FILE, [])


def update_watchlist(watchlist: List[Dict]) -> bool:
    """Update the entire watchlist (used by availability checker)."""
    return _save_json(AVAILABILITY_WATCH_FILE, watchlist)


def remove_from_watchlist(media_id: int, media_type: str, season: int = None) -> bool:
    """Remove a specific item from watchlist."""
    watchlist = _load_json(AVAILABILITY_WATCH_FILE, [])

    original_len = len(watchlist)
    watchlist = [
        w for w in watchlist
        if not (w.get("media_id") == media_id and
                w.get("media_type") == media_type and
                w.get("season") == season)
    ]

    if len(watchlist) < original_len:
        success = _save_json(AVAILABILITY_WATCH_FILE, watchlist)
        if success:
            logger.info(f"Removed from watchlist: {media_id} ({media_type})")
        return success

    return False


# ============================================================================
# User Statistics
# ============================================================================

def get_user_stats(limit: int = None) -> List[Dict]:
    """Get user statistics from mapping table."""
    conn = _get_postgres_connection()
    if not conn:
        logger.debug("Postgres not available for user stats")
        return []

    try:
        cursor = conn.cursor()
        sql = f"""
            SELECT
                telegram_user_id,
                telegram_username,
                telegram_display_name,
                request_count,
                TO_CHAR(first_seen, 'YYYY-MM-DD HH24:MI:SS') as first_seen,
                TO_CHAR(last_seen, 'YYYY-MM-DD HH24:MI:SS') as last_seen,
                TO_CHAR(last_request_at, 'YYYY-MM-DD HH24:MI:SS') as last_request_at
            FROM {POSTGRES_SCHEMA}.telegram_user_mapping
            ORDER BY request_count DESC
        """
        if limit:
            sql += f" LIMIT {limit}"

        cursor.execute(sql)
        rows = cursor.fetchall()
        cursor.close()

        results = []
        for row in rows:
            results.append({
                "user_id": row[0],
                "username": row[1],
                "display_name": row[2],
                "request_count": row[3],
                "first_seen": row[4],
                "last_seen": row[5],
                "last_request_at": row[6]
            })

        return results
    except Exception as e:
        logger.error(f"Failed to get user stats: {e}")
        return []


# ============================================================================
# Request Size Limits
# ============================================================================

def get_user_watchlist_count(user_id: int) -> Dict[str, int]:
    """
    Get count of pending items in watchlist by user.

    Tracks limits per user (telegram_user_id), not per chat.
    This allows proper limit enforcement even in group chats.

    Args:
        user_id: Telegram user ID

    Returns:
        {"movies": 5, "tv_shows": 3, "total": 8}
    """
    watchlist = get_watchlist()

    movies = sum(1 for w in watchlist
                 if w.get("telegram_user_id") == user_id
                 and w.get("media_type") == "movie"
                 and w.get("last_known_status") not in ("complete_notified", "notified"))

    tv_shows = sum(1 for w in watchlist
                   if w.get("telegram_user_id") == user_id
                   and w.get("media_type") == "tv"
                   and w.get("last_known_status") not in ("complete_notified", "notified"))

    total = movies + tv_shows

    logger.debug(f"User {user_id} watchlist: {movies} movies, {tv_shows} TV shows, {total} total")

    return {"movies": movies, "tv_shows": tv_shows, "total": total}


def can_add_to_watchlist(user_id: int, media_type: str) -> tuple[bool, str]:
    """
    Check if user can add more items to watchlist.
    Tracks limits per user, not per chat (works for both DMs and group chats).

    Args:
        user_id: Telegram/Discord user ID (not chat ID)
        media_type: "movie" or "tv"

    Returns:
        (can_add: bool, message: str)
    """
    # Check if user is banned
    is_banned, ban_reason = is_user_banned(user_id)
    if is_banned:
        return False, (
            f"🚫 **Access Denied**\n\n"
            f"You have been banned from making requests.\n"
            f"Reason: {ban_reason}"
        )

    # Get user's quota (custom or default)
    quota = get_user_quota(user_id)
    MAX_MOVIES = quota["movies"]
    MAX_TV_SHOWS = quota["tv_shows"]

    counts = get_user_watchlist_count(user_id)

    if media_type == "movie":
        if counts["movies"] >= MAX_MOVIES:
            return False, (
                f"⚠️ **Request Limit Reached**\n\n"
                f"You have {counts['movies']} pending movies (limit: {MAX_MOVIES}).\n"
                f"Please wait for some to become available before requesting more."
            )
    elif media_type == "tv":
        if counts["tv_shows"] >= MAX_TV_SHOWS:
            return False, (
                f"⚠️ **Request Limit Reached**\n\n"
                f"You have {counts['tv_shows']} pending TV shows (limit: {MAX_TV_SHOWS}).\n"
                f"Please wait for some to become available before requesting more."
            )

    return True, ""


# ============================================================================
# Request Priority Management
# ============================================================================

PRIORITY_LEVELS = {"high": 1, "normal": 2, "low": 3}
PRIORITY_EMOJIS = {"high": "🔴", "normal": "⚪", "low": "🔵"}


def set_request_priority(request_id: int, priority: str) -> bool:
    """
    Set priority for a specific request.

    Args:
        request_id: Database request ID
        priority: "high", "normal", or "low"

    Returns:
        True if successful, False otherwise
    """
    if priority not in PRIORITY_LEVELS:
        logger.error(f"Invalid priority: {priority}")
        return False

    conn = _get_postgres_connection()
    if not conn:
        logger.warning("Postgres not available for setting priority")
        return False

    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE {POSTGRES_SCHEMA}.telegram_requests
            SET priority = %s
            WHERE id = %s
            """,
            (priority, request_id)
        )
        conn.commit()
        cursor.close()
        logger.info(f"Set priority={priority} for request_id={request_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to set priority: {e}")
        try:
            conn.rollback()
        except:
            pass
        return False


def get_pending_requests_by_priority(limit: int = 50) -> Dict[str, List[Dict]]:
    """
    Get pending requests grouped by priority.

    Returns:
        {"high": [...], "normal": [...], "low": [...]}
    """
    conn = _get_postgres_connection()
    if not conn:
        logger.warning("Postgres not available for getting pending requests")
        return {"high": [], "normal": [], "low": []}

    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, user_name, title, media_type, season, library_name,
                   TO_CHAR(requested_at, 'YYYY-MM-DD HH24:MI:SS') as timestamp,
                   tmdb_id, COALESCE(priority, 'normal') as priority,
                   COALESCE(request_status, 'PENDING') as status
            FROM {POSTGRES_SCHEMA}.telegram_requests
            ORDER BY
                CASE COALESCE(priority, 'normal')
                    WHEN 'high' THEN 1
                    WHEN 'normal' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 2
                END,
                requested_at DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        cursor.close()

        results = {"high": [], "normal": [], "low": []}
        for row in rows:
            request = {
                "id": row[0],
                "user": row[1],
                "title": row[2],
                "type": row[3],
                "season": row[4],
                "library": row[5],
                "timestamp": row[6],
                "tmdb_id": row[7],
                # overseerr_request_id removed
                "priority": row[8],
                "status": row[9]
            }
            priority = row[8]
            if priority in results:
                results[priority].append(request)

        return results
    except Exception as e:
        logger.error(f"Failed to get pending requests by priority: {e}")
        return {"high": [], "normal": [], "low": []}


def get_priority_emoji(priority: str) -> str:
    """Get emoji for priority level."""
    return PRIORITY_EMOJIS.get(priority, "⚪")

# ============================================================================
# Ban Management
# ============================================================================

# File paths for ban and quota management
import os
from config import DATA_DIR
BANNED_USERS_FILE = os.path.join(DATA_DIR, "banned_users.json")
USER_QUOTAS_FILE = os.path.join(DATA_DIR, "user_quotas.json")

# Default quotas
MAX_MOVIES = 5
MAX_TV_SHOWS = 5


def add_banned_user(user_id: int, reason: str = "No reason provided", banned_by: str = "Admin") -> bool:
    """
    Ban a user from making requests.
    
    Args:
        user_id: Discord/Telegram user ID
        reason: Reason for ban
        banned_by: Who banned them
        
    Returns:
        True if successful
    """
    banned_users = _load_json(BANNED_USERS_FILE, {})
    
    banned_users[str(user_id)] = {
        "user_id": user_id,
        "reason": reason,
        "banned_by": banned_by,
        "banned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    success = _save_json(BANNED_USERS_FILE, banned_users)
    if success:
        logger.info(f"Banned user {user_id}: {reason}")
    return success


def remove_banned_user(user_id: int) -> bool:
    """
    Unban a user.
    
    Args:
        user_id: Discord/Telegram user ID
        
    Returns:
        True if successful
    """
    banned_users = _load_json(BANNED_USERS_FILE, {})
    
    if str(user_id) in banned_users:
        del banned_users[str(user_id)]
        success = _save_json(BANNED_USERS_FILE, banned_users)
        if success:
            logger.info(f"Unbanned user {user_id}")
        return success
    
    return True  # Already not banned


def is_user_banned(user_id: int) -> tuple[bool, str]:
    """
    Check if user is banned.
    
    Args:
        user_id: Discord/Telegram user ID
        
    Returns:
        (is_banned, reason)
    """
    banned_users = _load_json(BANNED_USERS_FILE, {})
    
    if str(user_id) in banned_users:
        ban_info = banned_users[str(user_id)]
        reason = ban_info.get("reason", "No reason provided")
        return True, reason
    
    return False, ""


def get_banned_users() -> List[Dict]:
    """
    Get list of all banned users.
    
    Returns:
        List of ban records
    """
    banned_users = _load_json(BANNED_USERS_FILE, {})
    return list(banned_users.values())


# ============================================================================
# Quota Management
# ============================================================================

def set_user_quota(user_id: int, movies: int = None, tv_shows: int = None) -> bool:
    """
    Set custom quota for a user.
    
    Args:
        user_id: Discord/Telegram user ID
        movies: Custom movie quota (None = use default)
        tv_shows: Custom TV show quota (None = use default)
        
    Returns:
        True if successful
    """
    quotas = _load_json(USER_QUOTAS_FILE, {})
    
    user_quota = quotas.get(str(user_id), {})
    
    if movies is not None:
        user_quota["movies"] = movies
    if tv_shows is not None:
        user_quota["tv_shows"] = tv_shows
    
    quotas[str(user_id)] = user_quota
    
    success = _save_json(USER_QUOTAS_FILE, quotas)
    if success:
        logger.info(f"Set quota for user {user_id}: movies={movies}, tv={tv_shows}")
    return success


def get_user_quota(user_id: int) -> Dict[str, int]:
    """
    Get quota limits for a user (custom or default).
    
    Args:
        user_id: Discord/Telegram user ID
        
    Returns:
        {"movies": X, "tv_shows": Y}
    """
    quotas = _load_json(USER_QUOTAS_FILE, {})
    user_quota = quotas.get(str(user_id), {})
    
    return {
        "movies": user_quota.get("movies", MAX_MOVIES),
        "tv_shows": user_quota.get("tv_shows", MAX_TV_SHOWS)
    }


def reset_user_quota(user_id: int) -> bool:
    """
    Reset user to default quotas.
    
    Args:
        user_id: Discord/Telegram user ID
        
    Returns:
        True if successful
    """
    quotas = _load_json(USER_QUOTAS_FILE, {})
    
    if str(user_id) in quotas:
        del quotas[str(user_id)]
        success = _save_json(USER_QUOTAS_FILE, quotas)
        if success:
            logger.info(f"Reset quota for user {user_id} to defaults")
        return success
    
    return True  # Already at defaults
