#!/usr/bin/env python3
"""
Radarr/Sonarr Status Sync Script

Checks all database requests against Radarr and Sonarr to update their status.
Works by searching for titles directly in Radarr/Sonarr APIs.
"""

import os
import sys
import logging
import psycopg2
import requests
import time
from datetime import datetime
from typing import Optional, Dict, Tuple

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DATABASE,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_SCHEMA,
    POSTGRES_ENABLED,
    RADARR_API_URL,
    RADARR_API_KEY,
    SONARR_API_URL,
    SONARR_API_KEY
)

# Ensure log directory exists
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "sync_radarr_sonarr_status.log"), mode="a"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Get PostgreSQL database connection."""
    if not POSTGRES_ENABLED:
        logger.error("PostgreSQL is not enabled in configuration")
        return None

    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DATABASE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        conn.autocommit = True  # prevent stale idle-in-transaction
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None


def search_radarr_by_title(title: str) -> Optional[Dict]:
    """Search for a movie in Radarr by title."""
    try:
        url = f"{RADARR_API_URL}/api/v3/movie"
        params = {"apikey": RADARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        movies = response.json()

        # Clean title for comparison (remove year)
        search_title = title.split('(')[0].strip().lower()

        # Find best match
        for movie in movies:
            movie_title = movie.get('title', '').lower()
            if search_title in movie_title or movie_title in search_title:
                return movie

        return None

    except Exception as e:
        logger.error(f"Error searching Radarr for '{title}': {e}")
        return None


def search_sonarr_by_title(title: str) -> Optional[Dict]:
    """Search for a TV show in Sonarr by title."""
    try:
        url = f"{SONARR_API_URL}/api/v3/series"
        params = {"apikey": SONARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        series = response.json()

        # Clean title for comparison (remove year and season info)
        search_title = title.split('(')[0].strip().lower()

        # Find best match
        for show in series:
            show_title = show.get('title', '').lower()
            if search_title in show_title or show_title in search_title:
                return show

        return None

    except Exception as e:
        logger.error(f"Error searching Sonarr for '{title}': {e}")
        return None


def get_sonarr_episodes(series_id: int, season_number: Optional[int] = None) -> Tuple[int, int]:
    """
    Get episode counts for a series in Sonarr.

    Returns:
        (episodes_with_files, total_episodes)
    """
    try:
        url = f"{SONARR_API_URL}/api/v3/episode"
        params = {"apikey": SONARR_API_KEY, "seriesId": series_id}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        episodes = response.json()

        if season_number is not None:
            episodes = [ep for ep in episodes if ep.get("seasonNumber") == season_number]

        total = len(episodes)
        with_files = sum(1 for ep in episodes if ep.get("hasFile", False))

        return with_files, total

    except Exception as e:
        logger.error(f"Error getting Sonarr episodes for series {series_id}: {e}")
        return 0, 0


def determine_status(media_type: str, title: str, season: Optional[int] = None) -> Tuple[str, str]:
    """
    Determine status of a request by checking Radarr/Sonarr.

    Returns:
        (status, reason)
    """
    if media_type == "movie":
        # Search Radarr
        movie = search_radarr_by_title(title)

        if not movie:
            return "NOT_IN_RADARR", f"Not found in Radarr"

        has_file = movie.get("hasFile", False)
        monitored = movie.get("monitored", False)

        if has_file:
            return "AVAILABLE", f"File exists in Radarr"
        elif monitored:
            return "PENDING", f"Monitored in Radarr, no file yet"
        else:
            return "PENDING", f"In Radarr but not monitored"

    else:  # TV show
        # Search Sonarr
        show = search_sonarr_by_title(title)

        if not show:
            return "NOT_IN_SONARR", f"Not found in Sonarr"

        series_id = show.get("id")
        monitored = show.get("monitored", False)

        # Get episode counts
        episodes_with_files, total_episodes = get_sonarr_episodes(series_id, season)

        if total_episodes == 0:
            return "PENDING", f"In Sonarr, no episodes tracked yet"

        if episodes_with_files == total_episodes:
            return "AVAILABLE", f"All {total_episodes} episodes available"
        elif episodes_with_files > 0:
            return "PARTIALLY_AVAILABLE", f"{episodes_with_files}/{total_episodes} episodes available"
        elif monitored:
            return "PENDING", f"Monitored in Sonarr, 0/{total_episodes} episodes"
        else:
            return "PENDING", f"In Sonarr but not monitored"


def sync_single_request(conn, request_id: int, media_type: str, title: str, season: Optional[int]) -> bool:
    """Sync status for a single request."""
    try:
        status, reason = determine_status(media_type, title, season)

        # Update database
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE {POSTGRES_SCHEMA}.telegram_requests
            SET request_status = %s,
                last_status_check = NOW()
            WHERE id = %s
            """,
            (status, request_id)
        )
        conn.commit()
        cursor.close()

        logger.info(f"[{media_type.upper()}] {title}: {status} - {reason}")
        return True

    except Exception as e:
        logger.error(f"Failed to sync request {request_id}: {e}")
        try:
            conn.rollback()
        except:
            pass
        return False


def sync_all_requests():
    """Main sync function."""
    logger.info("=" * 80)
    logger.info("Starting Radarr/Sonarr status sync")
    logger.info("=" * 80)

    conn = get_db_connection()
    if not conn:
        logger.error("Cannot proceed without database connection")
        return

    try:
        # Get all requests
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, title, media_type, season, request_status
            FROM {POSTGRES_SCHEMA}.telegram_requests
            ORDER BY requested_at DESC
            """
        )
        requests = cursor.fetchall()
        cursor.close()

        logger.info(f"Found {len(requests)} requests to check")

        # Stats
        updated = 0
        errors = 0
        status_counts = {}

        for i, (req_id, title, media_type, season, current_status) in enumerate(requests, 1):
            logger.info(f"[{i}/{len(requests)}] Checking: {title}")

            if sync_single_request(conn, req_id, media_type, title, season):
                updated += 1

                # Get updated status for stats
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT request_status FROM {POSTGRES_SCHEMA}.telegram_requests WHERE id = %s",
                    (req_id,)
                )
                new_status = cursor.fetchone()[0]
                cursor.close()

                status_counts[new_status] = status_counts.get(new_status, 0) + 1
            else:
                errors += 1

            # Rate limiting
            time.sleep(0.2)

        logger.info("=" * 80)
        logger.info("Sync complete:")
        logger.info(f"  - Total processed: {len(requests)}")
        logger.info(f"  - Updated: {updated}")
        logger.info(f"  - Errors: {errors}")
        logger.info("")
        logger.info("Status breakdown:")
        for status, count in sorted(status_counts.items()):
            logger.info(f"  - {status}: {count}")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Sync failed with error: {e}", exc_info=True)
    finally:
        try:
            conn.close()
        except:
            pass


if __name__ == "__main__":
    try:
        sync_all_requests()
    except KeyboardInterrupt:
        logger.info("Sync interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
