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
            logger.info("Postgres connection established for request logging")
        except Exception as e:
            logger.error(f"Failed to connect to Postgres: {e}")
            _pg_enabled = False  # Disable for this session
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
# Request Logging
# ============================================================================

def log_request(
    telegram_user: str,
    media_title: str,
    media_type: str,
    season: int = None,
    library_name: str = None,
    tmdb_id: int = None,
    overseerr_request_id: int = None
) -> bool:
    """
    Save request info with timestamp to both Postgres and JSON.
    Postgres is primary, JSON is backup.
    """
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
    }
    
    # Try Postgres first
    pg_success = False
    conn = _get_postgres_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO {POSTGRES_SCHEMA}.telegram_requests 
                (user_name, title, media_type, season, library_name, requested_at, tmdb_id, overseerr_request_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (telegram_user, media_title, media_type, season, library_name, timestamp, tmdb_id, overseerr_request_id)
            )
            conn.commit()
            cursor.close()
            pg_success = True
            logger.info(f"[PG] Logged request: {media_title} by {telegram_user}")
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
                       tmdb_id, overseerr_request_id
                FROM {POSTGRES_SCHEMA}.telegram_requests
                WHERE user_name = %s
                ORDER BY requested_at DESC
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
                    "overseerr_request_id": row[8],
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
                SELECT user_name, title, media_type, season, library_name,
                       TO_CHAR(requested_at, 'YYYY-MM-DD HH24:MI:SS') as timestamp
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
                    "user": row[0],
                    "title": row[1],
                    "type": row[2],
                    "season": row[3],
                    "library": row[4],
                    "timestamp": row[5]
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
    season: int = None
) -> bool:
    """Add an item to the availability watchlist."""
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
        return True
    
    entry = {
        "media_id": media_id,
        "media_type": media_type,
        "season": season,
        "chat_id": chat_id,
        "title": title,
        "library_name": library_name,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_known_status": None,
    }
    
    watchlist.append(entry)
    success = _save_json(AVAILABILITY_WATCH_FILE, watchlist)
    
    if success:
        logger.info(f"Added to watchlist: {title}")
    return success


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
