#!/usr/bin/env python3
"""
Request Status Sync Script v2 (2026-05-23 rewrite)

Old approach: matched local rows against Seerr's /request list (paginated).
Failed silently when Seerr dropped completed requests from the list →
"delivered but stuck PENDING" rows accumulated.

New approach (this file):
  1. Pre-fetch full Radarr and Sonarr inventories
  2. For each local row, check on-disk state in Radarr/Sonarr (primary truth)
  3. For Sonarr, support both tmdb_id and tvdb_id lookups (some series have
     tmdbId=0 in Sonarr because TMDB linkage isn't always populated)
  4. Fall back to Seerr's request status if not in Radarr/Sonarr
  5. Mark FAILED rows that aren't anywhere
"""

import os
import sys
import time
import logging
from typing import Dict, Optional, Tuple

import requests
import psycopg2

# Bot project for shared modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (  # noqa
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD,
    POSTGRES_SCHEMA, POSTGRES_ENABLED,
    RADARR_API_URL, RADARR_API_KEY,
    SONARR_API_URL, SONARR_API_KEY,
)
from overseerr_api import overseerr_request  # noqa  — used as fallback for TVDB resolution

# Logging
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "sync_request_status.log"), mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# Seerr status code mapping (used only for fallback path)
SEERR_STATUS_MAP = {1: "PENDING", 2: "PROCESSING", 3: "PARTIALLY_AVAILABLE", 4: "AVAILABLE"}


def get_db_connection():
    if not POSTGRES_ENABLED:
        logger.error("PostgreSQL is not enabled in configuration")
        return None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST, port=POSTGRES_PORT, database=POSTGRES_DATABASE,
            user=POSTGRES_USER, password=POSTGRES_PASSWORD,
        )
        conn.autocommit = True  # prevent stale idle-in-transaction
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None


def fetch_radarr_inventory() -> Dict[int, dict]:
    """Returns {tmdb_id: movie_dict}. Skips records without a TMDB ID."""
    r = requests.get(f"{RADARR_API_URL}/api/v3/movie",
                     headers={"X-Api-Key": RADARR_API_KEY}, timeout=20)
    r.raise_for_status()
    movies = r.json()
    by_tmdb = {m["tmdbId"]: m for m in movies if m.get("tmdbId")}
    logger.info(f"Radarr inventory: {len(by_tmdb)} movies (of {len(movies)} total)")
    return by_tmdb


def fetch_sonarr_inventory() -> Tuple[Dict[int, dict], Dict[int, dict]]:
    """Returns ({tmdb_id: series}, {tvdb_id: series}) — same series may appear in both."""
    r = requests.get(f"{SONARR_API_URL}/api/v3/series",
                     headers={"X-Api-Key": SONARR_API_KEY}, timeout=20)
    r.raise_for_status()
    series = r.json()
    by_tmdb = {s["tmdbId"]: s for s in series if s.get("tmdbId")}
    by_tvdb = {s["tvdbId"]: s for s in series if s.get("tvdbId")}
    logger.info(f"Sonarr inventory: {len(series)} series ({len(by_tmdb)} indexed by tmdb, {len(by_tvdb)} by tvdb)")
    return by_tmdb, by_tvdb


def resolve_tvdb_via_seerr(tmdb_id: int) -> Optional[int]:
    """Ask Seerr for TVDB ID corresponding to a TMDB ID (TV only). Returns None on failure."""
    try:
        resp = overseerr_request("GET", f"/tv/{tmdb_id}")
        data = resp.json()
        ext = data.get("externalIds") or {}
        return ext.get("tvdbId")
    except Exception as e:
        logger.debug(f"TVDB resolution via Seerr failed for tmdb={tmdb_id}: {e}")
        return None


def determine_movie_status(radarr_movie: dict) -> str:
    """Map a Radarr movie record to our request_status."""
    if radarr_movie.get("hasFile"):
        return "AVAILABLE"
    radarr_status = radarr_movie.get("status", "")
    if radarr_status in ("inCinemas", "released"):
        return "PROCESSING"  # exists in indexers (or should), Radarr searching
    # announced, deleted, etc.
    return "PENDING"


def determine_series_status(sonarr_series: dict, season: Optional[int]) -> str:
    """Map a Sonarr series record to our request_status. If season is given, scope to it."""
    seasons_info = sonarr_series.get("seasons", []) or []
    if season is not None:
        season_data = next((s for s in seasons_info if s.get("seasonNumber") == season), None)
        if not season_data:
            return "PENDING"  # requested season not even tracked in Sonarr
        sstats = season_data.get("statistics", {}) or {}
        files = sstats.get("episodeFileCount", 0)
        total = sstats.get("episodeCount", 0)
        if total == 0:
            return "PENDING"  # season has no episodes yet (unreleased / unmonitored)
        if files >= total:
            return "AVAILABLE"
        if files > 0:
            return "PARTIALLY_AVAILABLE"
        return "PROCESSING"
    # Whole series (no specific season requested)
    stats = sonarr_series.get("statistics", {}) or {}
    files = stats.get("episodeFileCount", 0)
    total = stats.get("episodeCount", 0)
    if total == 0:
        return "PENDING"
    if files >= total:
        return "AVAILABLE"
    if files > 0:
        return "PARTIALLY_AVAILABLE"
    return "PROCESSING"


def fetch_seerr_status_for_media(tmdb_id: int, media_type: str) -> Optional[str]:
    """Fallback: ask Seerr directly for media availability. Returns one of our statuses or None."""
    try:
        path = f"/{media_type}/{tmdb_id}"
        resp = overseerr_request("GET", path)
        data = resp.json()
        media_info = data.get("mediaInfo") or {}
        seerr_status_code = media_info.get("status")
        if seerr_status_code is None:
            return None
        return SEERR_STATUS_MAP.get(seerr_status_code, "UNKNOWN")
    except Exception:
        return None


def get_active_rows(conn) -> list:
    """Fetch active (non-archived) rows that need a status check."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, tmdb_id, media_type, title, season, request_status, overseerr_request_id
        FROM {POSTGRES_SCHEMA}.telegram_requests
        WHERE archived = FALSE
          AND (
               request_status != 'AVAILABLE'
            OR last_status_check IS NULL
            OR last_status_check < NOW() - INTERVAL '10 minutes'
          )
        ORDER BY requested_at DESC
        LIMIT 500
    """)
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "id": r[0], "tmdb_id": r[1], "media_type": r[2], "title": r[3],
            "season": r[4], "request_status": r[5], "overseerr_request_id": r[6],
        }
        for r in rows
    ]


def update_row(conn, row_id: int, new_status: str, archive_if_available: bool = True) -> None:
    """Update a row's status. If new status is AVAILABLE, also archive it."""
    cur = conn.cursor()
    if new_status == "AVAILABLE" and archive_if_available:
        cur.execute(f"""
            UPDATE {POSTGRES_SCHEMA}.telegram_requests
            SET request_status = %s, archived = TRUE, last_status_check = NOW()
            WHERE id = %s
        """, (new_status, row_id))
    else:
        cur.execute(f"""
            UPDATE {POSTGRES_SCHEMA}.telegram_requests
            SET request_status = %s, last_status_check = NOW()
            WHERE id = %s
        """, (new_status, row_id))
    conn.commit()
    cur.close()


def bump_check_time(conn, row_id: int) -> None:
    cur = conn.cursor()
    cur.execute(f"""
        UPDATE {POSTGRES_SCHEMA}.telegram_requests
        SET last_status_check = NOW()
        WHERE id = %s
    """, (row_id,))
    conn.commit()
    cur.close()


def sync():
    logger.info("=" * 80)
    logger.info("Starting request status sync (v2 — Radarr/Sonarr-direct)")
    logger.info("=" * 80)

    conn = get_db_connection()
    if not conn:
        return

    try:
        radarr_by_tmdb = fetch_radarr_inventory()
        sonarr_by_tmdb, sonarr_by_tvdb = fetch_sonarr_inventory()

        rows = get_active_rows(conn)
        logger.info(f"Processing {len(rows)} active rows")

        counts = {"updated": 0, "unchanged": 0, "available": 0, "still_pending": 0, "not_found": 0}

        for row in rows:
            tmdb_id = row["tmdb_id"]
            media_type = row["media_type"]
            row_id = row["id"]
            current_status = row["request_status"]
            title = row["title"]
            season = row["season"]

            new_status: Optional[str] = None

            if tmdb_id is None:
                logger.debug(f"id={row_id} {title!r} has no tmdb_id — bumping check time only")
                bump_check_time(conn, row_id)
                counts["not_found"] += 1
                continue

            if media_type == "movie":
                movie = radarr_by_tmdb.get(tmdb_id)
                if movie:
                    new_status = determine_movie_status(movie)
                else:
                    # Fallback to Seerr
                    new_status = fetch_seerr_status_for_media(tmdb_id, "movie")
            else:  # tv
                series = sonarr_by_tmdb.get(tmdb_id)
                if not series:
                    # Sonarr has tmdbId=0 for some series — try tvdb fallback via Seerr
                    tvdb_id = resolve_tvdb_via_seerr(tmdb_id)
                    if tvdb_id:
                        series = sonarr_by_tvdb.get(tvdb_id)
                if series:
                    new_status = determine_series_status(series, season)
                else:
                    new_status = fetch_seerr_status_for_media(tmdb_id, "tv")

            if new_status is None:
                logger.warning(f"id={row_id} {title!r} (tmdb={tmdb_id}, {media_type}) not found anywhere — bumping check time")
                bump_check_time(conn, row_id)
                counts["not_found"] += 1
                continue

            if new_status != current_status:
                logger.info(f"id={row_id} {title!r}: {current_status} → {new_status}")
                update_row(conn, row_id, new_status)
                counts["updated"] += 1
                if new_status == "AVAILABLE":
                    counts["available"] += 1
            else:
                bump_check_time(conn, row_id)
                counts["unchanged"] += 1
                if new_status == "PENDING":
                    counts["still_pending"] += 1

            time.sleep(0.05)  # small breath between iterations

        logger.info("=" * 80)
        logger.info(f"Sync complete: updated={counts['updated']} (of which AVAILABLE={counts['available']}), "
                    f"unchanged={counts['unchanged']} (still_pending={counts['still_pending']}), "
                    f"not_found={counts['not_found']}")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sync()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Sync crashed: {e}")
        sys.exit(1)
