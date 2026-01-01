# radarr_sonarr_api.py
"""
Direct Radarr/Sonarr API integration for real-time availability checking.
"""
import logging
import requests
import time
from typing import Optional, Dict, Tuple
from functools import wraps

logger = logging.getLogger(__name__)


def retry_on_failure(max_retries=3, backoff_factor=2):
    """Decorator to retry API calls with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.RequestException as e:
                    if attempt == max_retries - 1:
                        logger.error(f"{func.__name__} failed after {max_retries} attempts: {e}")
                        raise
                    wait_time = backoff_factor ** attempt
                    logger.warning(f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
            return None
        return wrapper
    return decorator


class RadarrSonarrAPI:
    """Check media availability directly from Radarr/Sonarr APIs."""
    
    def __init__(self, radarr_url: str, radarr_key: str, sonarr_url: str, sonarr_key: str):
        self.radarr_url = radarr_url.rstrip('/')
        self.radarr_key = radarr_key
        self.sonarr_url = sonarr_url.rstrip('/')
        self.sonarr_key = sonarr_key
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})
    
    @retry_on_failure(max_retries=3, backoff_factor=2)
    def check_movie_availability(self, tmdb_id: int) -> Tuple[bool, Optional[Dict]]:
        """
        Check if a movie is available in Radarr.
        
        Args:
            tmdb_id: The TMDB ID of the movie
            
        Returns:
            Tuple of (is_available, movie_data)
            is_available: True if movie file exists on disk
            movie_data: Dict with movie info if found, None otherwise
        """
        try:
            url = f"{self.radarr_url}/api/v3/movie"
            params = {"apikey": self.radarr_key}
            
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            movies = response.json()
            
            # Find movie by tmdbId
            movie = next((m for m in movies if m.get("tmdbId") == tmdb_id), None)
            
            if not movie:
                logger.debug(f"Movie tmdbid={tmdb_id} not found in Radarr")
                return False, None
            
            # Check if movie has file
            has_file = movie.get("hasFile", False)
            
            logger.info(
                f"Radarr: '{movie.get('title')}' ({movie.get('year')}) - "
                f"hasFile={has_file}, monitored={movie.get('monitored')}"
            )
            
            return has_file, movie
            
        except Exception as e:
            logger.exception(f"Error checking Radarr for tmdbid={tmdb_id}: {e}")
            return False, None
    
    @retry_on_failure(max_retries=3, backoff_factor=2)
    def check_tv_availability(self, tvdb_id: int, season_number: Optional[int] = None) -> Tuple[bool, Optional[Dict]]:
        """
        Check if a TV show (or specific season) is available in Sonarr.
        
        Args:
            tvdb_id: The TVDB ID of the show
            season_number: Optional specific season to check
            
        Returns:
            Tuple of (is_available, show_data)
            is_available: True if show/season has all files
            show_data: Dict with show info if found, None otherwise
        """
        try:
            url = f"{self.sonarr_url}/api/v3/series"
            params = {"apikey": self.sonarr_key}
            
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            series = response.json()
            
            # Find show by tvdbId
            show = next((s for s in series if s.get("tvdbId") == tvdb_id), None)
            
            if not show:
                logger.debug(f"TV show tvdbid={tvdb_id} not found in Sonarr")
                return False, None
            
            series_id = show.get("id")
            
            # Get episode information
            episodes_url = f"{self.sonarr_url}/api/v3/episode"
            episodes_params = {"apikey": self.sonarr_key, "seriesId": series_id}
            
            episodes_response = self.session.get(episodes_url, params=episodes_params, timeout=10)
            episodes_response.raise_for_status()
            episodes = episodes_response.json()
            
            if season_number is not None:
                # Check specific season
                season_episodes = [ep for ep in episodes if ep.get("seasonNumber") == season_number]
                
                if not season_episodes:
                    logger.debug(f"No episodes found for season {season_number}")
                    return False, show
                
                # Check if all episodes in season have files
                total_episodes = len(season_episodes)
                episodes_with_files = sum(1 for ep in season_episodes if ep.get("hasFile", False))
                
                is_available = episodes_with_files > 0 and episodes_with_files == total_episodes
                
                logger.info(
                    f"Sonarr: '{show.get('title')}' S{season_number} - "
                    f"{episodes_with_files}/{total_episodes} episodes available"
                )
                
                return is_available, show
            else:
                # Check entire show
                total_episodes = len(episodes)
                episodes_with_files = sum(1 for ep in episodes if ep.get("hasFile", False))
                
                is_available = episodes_with_files > 0
                
                logger.info(
                    f"Sonarr: '{show.get('title')}' - "
                    f"{episodes_with_files}/{total_episodes} episodes available"
                )
                
                return is_available, show
            
        except Exception as e:
            logger.exception(f"Error checking Sonarr for tvdbid={tvdb_id}: {e}")
            return False, None


# Global instance
_radarr_sonarr_api: Optional[RadarrSonarrAPI] = None


def init_radarr_sonarr_api(radarr_url: str, radarr_key: str, sonarr_url: str, sonarr_key: str):
    """Initialize the global RadarrSonarrAPI instance."""
    global _radarr_sonarr_api
    _radarr_sonarr_api = RadarrSonarrAPI(radarr_url, radarr_key, sonarr_url, sonarr_key)
    logger.info(f"RadarrSonarrAPI initialized: {radarr_url}, {sonarr_url}")


def get_radarr_sonarr_api() -> Optional[RadarrSonarrAPI]:
    """Get the global RadarrSonarrAPI instance."""
    return _radarr_sonarr_api
