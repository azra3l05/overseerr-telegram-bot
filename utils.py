# utils.py
"""
Utility functions for media processing, availability checks, and formatting.
"""
import logging
import requests
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import TMDB_API_KEY, POSTGRES_ENABLED

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependencies
_postgres_checker = None

def _get_postgres_checker():
    """Lazy load postgres checker."""
    global _postgres_checker
    if _postgres_checker is None and POSTGRES_ENABLED:
        try:
            from postgres_checker import get_postgres_checker
            _postgres_checker = get_postgres_checker()
        except Exception as e:
            logger.warning(f"Could not load postgres_checker: {e}")
            _postgres_checker = False  # Mark as failed to avoid retry
    return _postgres_checker if _postgres_checker is not False else None


# ============================================================================
# Date/Year Extraction
# ============================================================================

def safe_year(date_str: Optional[str]) -> Optional[str]:
    """Extract year from a date string (YYYY-MM-DD or YYYY format)."""
    if not date_str:
        return None
    
    # Try parsing full date
    try:
        return str(datetime.strptime(date_str, "%Y-%m-%d").year)
    except Exception:
        pass
    
    # Try extracting first 4 digits
    if isinstance(date_str, str) and len(date_str) >= 4 and date_str[:4].isdigit():
        return date_str[:4]
    
    return None


def title_with_year_from_details(details: Dict[str, Any], media_type: str) -> str:
    """Build a display title with year: 'Title (2023)'"""
    title = details.get("title") or details.get("name") or "Unknown"
    
    if media_type == "movie":
        year = safe_year(details.get("releaseDate") or details.get("release_date"))
    else:
        year = safe_year(details.get("firstAirDate") or details.get("first_air_date"))
    
    return f"{title} ({year})" if year else title


# ============================================================================
# Availability Detection
# ============================================================================

def is_available(
    details: Dict[str, Any],
    media_type: str,
    season_number: Optional[int] = None
) -> bool:
    """
    Robust availability check with Postgres database integration.
    
    Checks (in order):
    1. Postgres database (if enabled) - actual Radarr/Sonarr data
    2. Overseerr API status - fallback if Postgres unavailable
    
    Treats MediaStatus 4 (AVAILABLE) and 3 (PARTIALLY_AVAILABLE) as available.
    Accepts string statuses like "AVAILABLE", "PARTIALLY_AVAILABLE", "READY".
    For TV, checks the requested season specifically (if provided).
    """
    
    # Try Postgres first (if enabled) - most accurate
    pg_checker = _get_postgres_checker()
    if pg_checker:
        try:
            if media_type == "movie":
                # Get TMDB ID from details
                tmdb_id = details.get("tmdbId") or details.get("id")
                if tmdb_id:
                    is_avail, movie_data = pg_checker.check_movie_availability(int(tmdb_id))
                    if movie_data:  # Found in database
                        logger.info(f"Postgres check: Movie tmdbid={tmdb_id} available={is_avail}")
                        return is_avail
                    else:
                        logger.debug(f"Movie tmdbid={tmdb_id} not in Radarr database yet")
            
            elif media_type == "tv":
                # Try to get TVDB ID (Sonarr uses TVDB)
                tvdb_id = details.get("externalIds", {}).get("tvdbId") or details.get("tvdbId")
                if tvdb_id:
                    is_avail, show_data = pg_checker.check_tv_availability(
                        tvdb_id=int(tvdb_id), 
                        season_number=season_number
                    )
                    if show_data:  # Found in database
                        logger.info(
                            f"Postgres check: TV show tvdbid={tvdb_id} "
                            f"season={season_number} available={is_avail}"
                        )
                        return is_avail
                    else:
                        logger.debug(f"TV show tvdbid={tvdb_id} not in Sonarr database yet")
                else:
                    logger.debug(f"No TVDB ID found in details for TV availability check")
        
        except Exception as e:
            logger.exception(f"Postgres availability check failed, falling back to Overseerr: {e}")
    
    # Fallback to Overseerr API check (original logic)
    media_info = details.get("mediaInfo") or details.get("media") or {}
    
    # Normalize status from Overseerr (string OR numeric)
    status_raw = media_info.get("status")
    status_str = str(status_raw).upper() if status_raw is not None else ""
    status_num = None
    try:
        status_num = int(status_raw)
    except Exception:
        pass
    
    def is_available_status(s_str: str, s_num: Optional[int]) -> bool:
        """Check if a status indicates availability."""
        # Numeric enum: 4 = AVAILABLE, 3 = PARTIALLY_AVAILABLE
        if s_num in (3, 4):
            return True
        # String enum
        if s_str in {"AVAILABLE", "PARTIALLY_AVAILABLE", "READY"}:
            return True
        return False
    
    # TV: check specific season if requested
    if media_type == "tv":
        seasons = details.get("seasons") or []
        
        if season_number is not None:
            # Check the specific season
            for s in seasons:
                if int(s.get("seasonNumber", -1)) == int(season_number):
                    s_raw = s.get("status")
                    s_str = str(s_raw).upper() if s_raw is not None else ""
                    s_num = None
                    try:
                        s_num = int(s_raw)
                    except Exception:
                        pass
                    
                    # Check episode availability
                    ep_avail = s.get("episodesAvailable")
                    ep_total = s.get("episodeCount")
                    
                    if (is_available_status(s_str, s_num) or
                        (isinstance(ep_avail, int) and isinstance(ep_total, int) and 
                         ep_total > 0 and ep_avail >= ep_total)):
                        return True
            
            # Season not found or not available, check media-level status
            return is_available_status(status_str, status_num)
        else:
            # No specific season: check if ANY season is available
            for s in seasons:
                s_raw = s.get("status")
                s_str = str(s_raw).upper() if s_raw is not None else ""
                s_num = None
                try:
                    s_num = int(s_raw)
                except Exception:
                    pass
                
                ep_avail = s.get("episodesAvailable")
                ep_total = s.get("episodeCount")
                
                if (is_available_status(s_str, s_num) or
                    (isinstance(ep_avail, int) and isinstance(ep_total, int) and 
                     ep_total > 0 and ep_avail >= ep_total)):
                    return True
            
            return is_available_status(status_str, status_num)
    
    # Movies: media-level status is enough
    if is_available_status(status_str, status_num):
        return True
    
    # Fallback: check for library IDs
    for key in ("plexId", "ratingKey", "jellyfinId", "mediaId", "tmdbId"):
        if details.get(key) or media_info.get(key):
            return True
    
    # Boolean flag fallback
    if media_info.get("isAvailable") is True:
        return True
    
    return False


# ============================================================================
# IMDb URL Extraction
# ============================================================================

def imdb_url_from_details(details: Dict[str, Any]) -> Optional[str]:
    """
    Extract IMDb URL from details dict.
    Checks various possible fields and nested structures.
    """
    if not details or not isinstance(details, dict):
        return None
    
    # Direct field
    imdb_id = details.get("imdbId") or details.get("imdb_id")
    
    if not imdb_id:
        # Nested external ids
        ext = details.get("externalIds") or details.get("external_ids") or {}
        if isinstance(ext, dict):
            imdb_id = ext.get("imdb_id") or ext.get("imdbId") or ext.get("imdb")
    
    if imdb_id and isinstance(imdb_id, str) and imdb_id.startswith("tt"):
        return f"https://www.imdb.com/title/{imdb_id}"
    
    if imdb_id and isinstance(imdb_id, str) and imdb_id.isdigit():
        # Numeric IMDb ID (rare but possible)
        return f"https://www.imdb.com/title/tt{imdb_id.zfill(7)}"
    
    return None


# ============================================================================
# TMDB Fallback Functions
# ============================================================================

def get_tmdb_details_or_none(tmdb_id: int, media_type: str) -> Optional[Dict]:
    """
    Fetch basic details from TMDB for fallback scenarios.
    Returns normalized dict compatible with send_rich_poster().
    """
    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not configured, cannot fetch TMDB details")
        return None
    
    try:
        typ = "movie" if media_type == "movie" else "tv"
        url = f"https://api.themoviedb.org/3/{typ}/{tmdb_id}"
        params = {
            "api_key": TMDB_API_KEY,
            "language": "en-US",
            "append_to_response": "videos,external_ids"
        }
        
        r = requests.get(url, params=params, timeout=6)
        r.raise_for_status()
        j = r.json()
        
        # Normalize structure
        details = {
            "id": j.get("id"),
            "title": j.get("title") or j.get("name"),
            "name": j.get("name"),
            "releaseDate": j.get("release_date") or j.get("first_air_date"),
            "firstAirDate": j.get("first_air_date"),
            "posterPath": j.get("poster_path"),
            "overview": j.get("overview"),
            "runtime": j.get("runtime") or (
                j.get("episode_run_time")[0] if j.get("episode_run_time") else None
            ),
            "genres": j.get("genres"),
            "videos": j.get("videos"),
            "externalIds": j.get("external_ids", {}),
            "tmdbId": j.get("id"),
            "voteAverage": j.get("vote_average"),
            "tagline": j.get("tagline"),
        }
        
        return details
    except Exception as e:
        logger.error(f"TMDB fetch failed for {media_type} {tmdb_id}: {e}")
        return None


def tmdb_search(query: str, media_type: str, limit: int = 5) -> List[Dict]:
    """
    Search TMDB directly (fallback when Overseerr fails).
    Returns list of results in normalized format.
    """
    if not TMDB_API_KEY:
        return []
    
    try:
        typ = "movie" if media_type == "movie" else "tv"
        url = f"https://api.themoviedb.org/3/search/{typ}"
        params = {
            "api_key": TMDB_API_KEY,
            "query": query,
            "language": "en-US",
            "page": 1
        }
        
        r = requests.get(url, params=params, timeout=6)
        r.raise_for_status()
        j = r.json()
        
        results = []
        for item in j.get("results", [])[:limit]:
            results.append({
                "id": item.get("id"),
                "title": item.get("title") or item.get("name"),
                "name": item.get("name"),
                "posterPath": item.get("poster_path"),
                "releaseDate": item.get("release_date"),
                "firstAirDate": item.get("first_air_date"),
                "media_type": media_type,
            })
        
        return results
    except Exception as e:
        logger.error(f"TMDB search failed for '{query}' ({media_type}): {e}")
        return []
