# postgres_checker.py
"""
Direct Postgres integration to check actual Radarr/Sonarr availability.
This bypasses Overseerr and checks the actual media server database.
"""
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, Dict, Tuple
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class PostgresChecker:
    """Check media availability directly from Radarr/Sonarr Postgres database."""
    
    def __init__(self, host: str, port: int, database: str, user: str, password: str, schema: str = "serverstats"):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.schema = schema
        self._connection = None
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connect_timeout=5
            )
            yield conn
        except Exception as e:
            logger.error(f"Postgres connection failed: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
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
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    query = f"""
                        SELECT 
                            tmdbid,
                            title,
                            year,
                            hasfile,
                            isavailable,
                            status,
                            path,
                            sizeondisk
                        FROM {self.schema}.radarr
                        WHERE tmdbid = %s
                    """
                    cursor.execute(query, (tmdb_id,))
                    result = cursor.fetchone()
                    
                    if not result:
                        logger.debug(f"Movie tmdbid={tmdb_id} not found in Radarr database")
                        return False, None
                    
                    # Movie is available if it has a file on disk
                    is_available = result.get('hasfile', False) is True
                    
                    logger.info(
                        f"Movie '{result['title']}' ({result['year']}) - "
                        f"hasfile={result['hasfile']}, status={result['status']}"
                    )
                    
                    return is_available, dict(result)
                    
        except Exception as e:
            logger.exception(f"Error checking movie availability for tmdbid={tmdb_id}: {e}")
            return False, None
    
    def check_tv_availability(self, tvdb_id: Optional[int] = None, tmdb_id: Optional[int] = None, 
                             season_number: Optional[int] = None) -> Tuple[bool, Optional[Dict]]:
        """
        Check if a TV show (or specific season) is available in Sonarr.
        
        Args:
            tvdb_id: The TVDB ID (if available)
            tmdb_id: The TMDB ID (fallback)
            season_number: Specific season to check (None = check if ANY season available)
            
        Returns:
            Tuple of (is_available, show_data)
            is_available: True if show/season has files on disk
            show_data: Dict with show info if found, None otherwise
        """
        if not tvdb_id and not tmdb_id:
            logger.error("Either tvdb_id or tmdb_id must be provided")
            return False, None
        
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    # Try TVDB first, then fall back to TMDB (less reliable for TV)
                    if tvdb_id:
                        query = f"""
                            SELECT 
                                id, tvdbid, title, year, status, path, seasons, monitored
                            FROM {self.schema}.sonarr
                            WHERE tvdbid = %s
                        """
                        cursor.execute(query, (tvdb_id,))
                    else:
                        # Note: Sonarr doesn't have tmdbid column, this is a limitation
                        logger.warning("TMDB lookup for TV shows not directly supported - use TVDB ID")
                        return False, None
                    
                    result = cursor.fetchone()
                    
                    if not result:
                        logger.debug(f"TV show tvdbid={tvdb_id} not found in Sonarr database")
                        return False, None
                    
                    seasons = result.get('seasons', [])
                    if not seasons:
                        logger.debug(f"TV show '{result['title']}' has no season data")
                        return False, dict(result)
                    
                    # Check specific season or any season
                    if season_number is not None:
                        # Check specific season
                        for season in seasons:
                            if season.get('seasonNumber') == season_number:
                                stats = season.get('statistics', {})
                                percent = stats.get('percentOfEpisodes', 0)
                                episode_count = stats.get('episodeFileCount', 0)
                                total_episodes = stats.get('totalEpisodeCount', 0)
                                
                                # Consider available if we have at least some episodes
                                # or 100% of episodes available
                                is_available = (percent == 100 or episode_count >= total_episodes) and episode_count > 0
                                
                                logger.info(
                                    f"TV show '{result['title']}' S{season_number:02d} - "
                                    f"{episode_count}/{total_episodes} episodes ({percent}%)"
                                )
                                
                                return is_available, dict(result)
                        
                        logger.debug(f"Season {season_number} not found for '{result['title']}'")
                        return False, dict(result)
                    else:
                        # Check if ANY season has files
                        has_any_files = False
                        for season in seasons:
                            stats = season.get('statistics', {})
                            if stats.get('episodeFileCount', 0) > 0:
                                has_any_files = True
                                break
                        
                        logger.info(
                            f"TV show '{result['title']}' - has_any_files={has_any_files}"
                        )
                        
                        return has_any_files, dict(result)
                    
        except Exception as e:
            logger.exception(f"Error checking TV availability for tvdbid={tvdb_id}: {e}")
            return False, None
    
    def test_connection(self) -> bool:
        """Test if the database connection works."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    logger.info("Postgres connection test successful")
                    return result[0] == 1
        except Exception as e:
            logger.error(f"Postgres connection test failed: {e}")
            return False
    
    def get_stats(self) -> Dict[str, int]:
        """Get quick stats about the database."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.radarr")
                    movie_count = cursor.fetchone()[0]
                    
                    cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.radarr WHERE hasfile = true")
                    movies_with_files = cursor.fetchone()[0]
                    
                    cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.sonarr")
                    show_count = cursor.fetchone()[0]
                    
                    return {
                        'total_movies': movie_count,
                        'movies_with_files': movies_with_files,
                        'total_shows': show_count
                    }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}


# Singleton instance (initialized in config or main)
_postgres_checker: Optional[PostgresChecker] = None


def init_postgres_checker(host: str, port: int, database: str, user: str, password: str, schema: str = "serverstats"):
    """Initialize the global PostgresChecker instance."""
    global _postgres_checker
    _postgres_checker = PostgresChecker(host, port, database, user, password, schema)
    logger.info(f"PostgresChecker initialized: {host}:{port}/{database}")


def get_postgres_checker() -> Optional[PostgresChecker]:
    """Get the global PostgresChecker instance."""
    return _postgres_checker
