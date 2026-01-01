# overseerr_api.py
import requests
import logging
import os
import urllib.parse
import time
from typing import Tuple, Dict, Any
from functools import wraps

from config import (
    OVERSEERR_API_URL,
    TELEGRAMBOT_USERNAME,
    TELEGRAMBOT_PASSWORD,
)

# Ensure log directory exists
LOG_DIR = "/home/azra3l/logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Use the file name (without .py) as the log file name
log_file = os.path.join(LOG_DIR, os.path.basename(__file__).replace(".py", ".log"))

logging.basicConfig(
    level=logging.INFO,  # change to DEBUG if you want verbose output
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, mode="a"),
        logging.StreamHandler()  # still print to console
    ]
)

logger = logging.getLogger(__name__)
logger.info("✅ Logging started for %s", __file__)


session = requests.Session()


def retry_on_failure(max_retries=3, backoff_factor=2, exceptions=(requests.exceptions.RequestException,)):
    """Decorator to retry functions with exponential backoff on failure."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        logger.error(f"{func.__name__} failed after {max_retries} attempts: {e}")
                        raise
                    wait_time = backoff_factor ** attempt
                    logger.warning(f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
            return None
        return wrapper
    return decorator


def get_session_cookie():
    """Login as telegrambot user and store session cookie."""
    login_url = f"{OVERSEERR_API_URL}/auth/local"
    data = {"email": TELEGRAMBOT_USERNAME, "password": TELEGRAMBOT_PASSWORD}

    resp = session.post(login_url, json=data)
    resp.raise_for_status()

    if "connect.sid" not in session.cookies.get_dict():
        raise Exception("❌ Failed to log in to Overseerr. Check credentials.")

    print("✅ Logged in as telegrambot user, session cookie acquired.")


@retry_on_failure(max_retries=3, backoff_factor=2)
def overseerr_request(method: str, endpoint: str, **kwargs) -> requests.Response:
    """Wrapper for Overseerr API calls with auto re-login on 401 Unauthorized."""
    url = f"{OVERSEERR_API_URL}{endpoint}"
    resp = session.request(method, url, **kwargs)

    if resp.status_code == 401:
        print("⚠️  Session expired, re-logging in...")
        get_session_cookie()
        resp = session.request(method, url, **kwargs)

    resp.raise_for_status()
    return resp


# Init session on import
get_session_cookie()


def debug_fetch_overseerr(url: str, params: dict = None, headers: dict = None) -> Any:
    """
    Fetch URL from Overseerr and pretty-print the JSON for debugging.
    Use this when you want to inspect the raw response for a particular media id.
    """
    try:
        resp = overseerr_request("GET", url, params=params or {}, headers=headers or {})
        data = resp.json()
    except Exception as e:
        print(f"[Overseerr DEBUG] request failed: {e}")
        return None

    import json
    pretty = json.dumps(data, indent=2)[:20000]  # truncated to keep logs sane
    print("[Overseerr DEBUG] full JSON response (truncated):")
    print(pretty)
    return data


def search_media(query: str, media_type: str):
    """Search endpoint wrapper — returns list of matching items with details."""
    encoded_query = urllib.parse.quote(query)
    resp = overseerr_request("GET", "/search", params={"query": encoded_query})
    results = resp.json().get("results", [])
    parsed = []
    for r in results:
        if r.get("mediaType") == media_type:
            parsed.append({
                # prefer tmdbId if available so IDs are consistent with TMDB
                "id": r.get("tmdbId", r.get("id")),
                "title": r.get("title") or r.get("name"),
                "posterPath": r.get("posterPath"),
                "releaseDate": r.get("releaseDate"),
                "firstAirDate": r.get("firstAirDate"),
                "voteAverage": r.get("voteAverage"),
                "overview": r.get("overview"),
            })
    return parsed


def get_details(media_id: int, media_type: str) -> dict:
    """Return the full JSON details for a media item (movie/tv)."""
    resp = overseerr_request("GET", f"/{media_type}/{media_id}")
    return resp.json()


def _search_for_keys(obj):
    """Recursive helper that looks for keys that look like status/available."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_lower = k.lower()
            if "status" in key_lower:
                return (v, f'key:{k}')
            if "available" in key_lower:
                # available may be boolean or string
                return (v, f'key:{k}')
            if isinstance(v, (dict, list)):
                result = _search_for_keys(v)
                if result:
                    return result
    elif isinstance(obj, list):
        for item in obj:
            result = _search_for_keys(item)
            if result:
                return result
    return None


def _normalize_status(raw_value) -> str:
    """
    Normalize different possible representations into canonical strings:
    - AVAILABLE
    - MISSING
    - PARTIALLY_AVAILABLE
    - PENDING
    - PROCESSING
    - DECLINED
    - UNKNOWN
    """
    if raw_value is None:
        return "UNKNOWN"

    # Boolean available flags
    if isinstance(raw_value, bool):
        return "AVAILABLE" if raw_value else "MISSING"

    # Numeric codes sometimes used by APIs (if you encounter them, add mappings)
    if isinstance(raw_value, (int, float)):
        # default fallback: non-zero -> AVAILABLE
        return "AVAILABLE" if raw_value != 0 else "MISSING"

    # String handling
    v = str(raw_value).strip().upper()

    # Common variations
    if v in {"AVAILABLE", "TRUE", "YES", "1"}:
        return "AVAILABLE"
    if v in {"MISSING", "FALSE", "NO", "0", "NOT AVAILABLE", "UNAVAILABLE"}:
        return "MISSING"
    if "PARTIAL" in v:
        return "PARTIALLY_AVAILABLE"
    if "PENDING" in v:
        return "PENDING"
    if "PROCESS" in v:
        return "PROCESSING"
    if "DECLIN" in v or "DENIED" in v:
        return "DECLINED"
    if "REQUEST" in v and "PENDING" in v:
        return "PENDING"

    # If it looks like a request.status object (e.g., "Available", "Missing", etc)
    # try mapping a few known values
    mapping = {
        "MEDIA_AVAILABLE": "AVAILABLE",
        "MEDIA_MISSING": "MISSING",
        "PARTIALLY_AVAILABLE": "PARTIALLY_AVAILABLE",
        "DECLINED": "DECLINED",
        "PENDING": "PENDING",
    }
    if v in mapping:
        return mapping[v]

    # fallback
    return v


def get_canonical_status(media, media_type: str | None = None, requested_seasons: list[int] | None = None, session_cookie: str | None = None):
    """
    Flexible canonical status resolver.

    Accepts either:
      - media: dict returned from get_media_details(...)
      - media: int (TMDb id) + media_type ("movie" | "tv")  -> will fetch details.

    Returns: (status, meta)
      - status ∈ {"AVAILABLE", "PARTIALLY_AVAILABLE", "PROCESSING", "PENDING", "UNKNOWN"}
      - meta: {"source": <string>} describing which path determined the result
    """
    # --- helpers -------------------------------------------------------------
    def _safe_get(obj, *path):
        cur = obj
        for p in path:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur

    STATUS_MAP = {
        1: "PENDING",
        2: "PROCESSING",
        3: "PARTIALLY_AVAILABLE",
        4: "AVAILABLE",
        True: "AVAILABLE",
        False: "UNKNOWN",
        "UNKNOWN": "UNKNOWN",
        "PENDING": "PENDING",
        "PROCESSING": "PROCESSING",
        "PARTIALLY_AVAILABLE": "PARTIALLY_AVAILABLE",
        "AVAILABLE": "AVAILABLE",
        "READY": "AVAILABLE",
        "COMPLETED": "AVAILABLE",
        "COMPLETE": "AVAILABLE",
        "DONE": "AVAILABLE",
        # Common series lifecycle strings from TMDb/Overseerr that are NOT availability:
        "RETURNING SERIES": "UNKNOWN",
        "ENDED": "UNKNOWN",
        "CANCELED": "UNKNOWN",
        "CANCELLED": "UNKNOWN",
        "IN PRODUCTION": "UNKNOWN",
    }

    # --- normalize input to a dict ------------------------------------------
    media_data = None
    if isinstance(media, dict):
        media_data = media
    else:
        # Assume TMDb id was passed
        media_id = media
        try:
            media_data = get_media_details(media_id, media_type, session_cookie=session_cookie)
        except Exception:
            media_data = {}

    if not isinstance(media_data, dict):
        return "UNKNOWN", {"source": "no_media_dict"}

    # --- direct paths to try -------------------------------------------------
    candidates = [
        ("mediaInfo.status", _safe_get(media_data, "mediaInfo", "status")),
        ("media.status",     _safe_get(media_data, "media", "status")),
        ("status",           _safe_get(media_data, "status")),
        ("mediaInfo.available",   _safe_get(media_data, "mediaInfo", "available")),
        ("media.available",       _safe_get(media_data, "media", "available")),
        ("mediaInfo.isAvailable", _safe_get(media_data, "mediaInfo", "isAvailable")),
        ("media.isAvailable",     _safe_get(media_data, "media", "isAvailable")),
    ]

    for path, value in candidates:
        if value is None:
            continue
        norm = STATUS_MAP.get(value, STATUS_MAP.get(str(value).upper(), "UNKNOWN"))
        if norm != "UNKNOWN":
            return norm, {"source": path}

    # --- TV season-level logic ----------------------------------------------
    container = (
        _safe_get(media_data, "mediaInfo")
        or _safe_get(media_data, "media")
        or media_data
    )
    seasons = container.get("seasons") if isinstance(container, dict) else None
    if isinstance(seasons, list) and seasons:
        def _is_full(s):
            return (
                s.get("status") == 4
                or s.get("status") == "AVAILABLE"
                or s.get("episodesAvailable", 0) >= s.get("episodeCount", 1)
            )

        if requested_seasons:
            matched = [s for s in seasons if s.get("seasonNumber") in requested_seasons and _is_full(s)]
            if len(matched) == len(requested_seasons):
                return "AVAILABLE", {"source": "seasons.requested_full"}
            elif matched:
                return "PARTIALLY_AVAILABLE", {"source": "seasons.requested_partial"}
        else:
            full = [s for s in seasons if _is_full(s)]
            if len(full) == len(seasons):
                return "AVAILABLE", {"source": "seasons.all_full"}
            elif full:
                return "PARTIALLY_AVAILABLE", {"source": "seasons.some_full"}

    # --- linked-library fallback (presence implies available) ----------------
    for key in ("plexId", "ratingKey", "jellyfinId", "tautulliId"):
        if _safe_get(media_data, "mediaInfo", key) or _safe_get(media_data, "media", key):
            return "AVAILABLE", {"source": f"id:{key}"}

    return "UNKNOWN", {"source": "fallback"}


def request_media(media_id: int, media_type: str, seasons=None, library_id=None):
    """Request media as telegrambot user."""
    data = {
        "mediaType": media_type,
        "mediaId": media_id,
    }
    if seasons:
        data["seasons"] = seasons
    if library_id:
        data["rootFolderId"] = library_id

    print("➡️ Sending request payload:", data)  # Debug log

    resp = overseerr_request("POST", "/request", json=data)
    print("⬅️ Overseerr response:", resp.text)  # Debug log
    resp.raise_for_status()
    return resp.json()


def delete_request(request_id: int):
    """Delete a request from Overseerr."""
    resp = overseerr_request("DELETE", f"/request/{request_id}")
    resp.raise_for_status()
    return resp.json()


# --- Compatibility shim so callers can always do: get_media_details(id, type) ---
def get_media_details(media_id, media_type, session_cookie=None):
    """
    Return Overseerr media details for a TMDb id.
    Tries existing helpers if present; otherwise falls back to a direct HTTP call.
    """
    # 1) Try known helper names with/without 'session_cookie'
    try:
        # common alt name
        from overseerr_api import get_details as _get_details  # self-import safe
    except Exception:
        _get_details = None

    try:
        # another common alt
        from overseerr_api import fetch_media_details as _fetch_details  # type: ignore
    except Exception:
        _fetch_details = None

    # Prefer get_details if present
    fn = _get_details or _fetch_details
    if fn:
        try:
            return fn(media_id, media_type, session_cookie=session_cookie)  # type: ignore
        except TypeError:
            # Helper doesn’t accept session_cookie → retry without it
            return fn(media_id, media_type)  # type: ignore

    # 2) Fallback: direct HTTP to Overseerr
    import requests
    from config import OVERSEERR_URL, OVERSEERR_API_KEY
    headers = {"X-Api-Key": OVERSEERR_API_KEY}
    t = "movie" if media_type == "movie" else "tv"
    resp = requests.get(f"{OVERSEERR_URL}/api/v1/{t}/{media_id}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_discover_content(media_type: str = "movie", page: int = 1):
    """Get popular/trending content from Overseerr discover endpoint."""
    # Overseerr uses plural form in endpoint
    endpoint_type = "movies" if media_type == "movie" else "tv"
    resp = overseerr_request("GET", f"/discover/{endpoint_type}", params={"page": page, "language": "en"})
    results = resp.json().get("results", [])
    parsed = []
    for r in results:
        parsed.append({
            "id": r.get("id"),
            "title": r.get("title") or r.get("name"),
            "posterPath": r.get("posterPath"),
            "releaseDate": r.get("releaseDate"),
            "firstAirDate": r.get("firstAirDate"),
            "voteAverage": r.get("voteAverage"),
            "overview": r.get("overview"),
            "mediaType": r.get("mediaType", media_type)
        })
    return parsed


def get_request_status(request_id: int):
    """Get status of a specific Overseerr request."""
    resp = overseerr_request("GET", f"/request/{request_id}")
    return resp.json()
