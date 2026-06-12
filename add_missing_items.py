#!/usr/bin/env python3
"""
Add missing items to Radarr and Sonarr

This script adds items that are not found in Radarr/Sonarr.
"""

import os
import sys
import requests
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import RADARR_API_URL, RADARR_API_KEY, SONARR_API_URL, SONARR_API_KEY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def search_radarr_movie(title, year=None):
    """Search for a movie in Radarr's lookup."""
    try:
        url = f"{RADARR_API_URL}/api/v3/movie/lookup"
        params = {"apikey": RADARR_API_KEY, "term": title}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        results = response.json()

        if not results:
            logger.warning(f"No results found for movie: {title}")
            return None

        # If year provided, try to match it
        if year:
            for movie in results:
                if movie.get('year') == year:
                    return movie

        # Return first result
        return results[0]

    except Exception as e:
        logger.error(f"Error searching Radarr for '{title}': {e}")
        return None


def search_sonarr_series(title, year=None):
    """Search for a TV series in Sonarr's lookup."""
    try:
        url = f"{SONARR_API_URL}/api/v3/series/lookup"
        params = {"apikey": SONARR_API_KEY, "term": title}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        results = response.json()

        if not results:
            logger.warning(f"No results found for series: {title}")
            return None

        # If year provided, try to match it
        if year:
            for series in results:
                if series.get('year') == year:
                    return series

        # Return first result
        return results[0]

    except Exception as e:
        logger.error(f"Error searching Sonarr for '{title}': {e}")
        return None


def get_radarr_root_folder():
    """Get the first available root folder in Radarr."""
    try:
        url = f"{RADARR_API_URL}/api/v3/rootfolder"
        params = {"apikey": RADARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        folders = response.json()

        if folders:
            return folders[0]['path']

        return None

    except Exception as e:
        logger.error(f"Error getting Radarr root folder: {e}")
        return None


def get_sonarr_root_folder():
    """Get the first available root folder in Sonarr."""
    try:
        url = f"{SONARR_API_URL}/api/v3/rootfolder"
        params = {"apikey": SONARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        folders = response.json()

        if folders:
            return folders[0]['path']

        return None

    except Exception as e:
        logger.error(f"Error getting Sonarr root folder: {e}")
        return None


def get_radarr_quality_profile():
    """Get the first quality profile in Radarr."""
    try:
        url = f"{RADARR_API_URL}/api/v3/qualityprofile"
        params = {"apikey": RADARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        profiles = response.json()

        if profiles:
            return profiles[0]['id']

        return None

    except Exception as e:
        logger.error(f"Error getting Radarr quality profile: {e}")
        return None


def get_sonarr_quality_profile():
    """Get the first quality profile in Sonarr."""
    try:
        url = f"{SONARR_API_URL}/api/v3/qualityprofile"
        params = {"apikey": SONARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        profiles = response.json()

        if profiles:
            return profiles[0]['id']

        return None

    except Exception as e:
        logger.error(f"Error getting Sonarr quality profile: {e}")
        return None


def add_movie_to_radarr(movie_data, root_folder, quality_profile):
    """Add a movie to Radarr."""
    try:
        url = f"{RADARR_API_URL}/api/v3/movie"
        params = {"apikey": RADARR_API_KEY}

        payload = {
            "title": movie_data['title'],
            "year": movie_data.get('year'),
            "tmdbId": movie_data.get('tmdbId'),
            "qualityProfileId": quality_profile,
            "rootFolderPath": root_folder,
            "monitored": True,
            "addOptions": {
                "searchForMovie": True
            }
        }

        response = requests.post(url, params=params, json=payload, timeout=15)
        response.raise_for_status()

        logger.info(f"✅ Added movie to Radarr: {movie_data['title']} ({movie_data.get('year')})")
        return True

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            logger.warning(f"⚠️  Movie already exists in Radarr: {movie_data['title']}")
            return False
        else:
            logger.error(f"❌ Error adding movie to Radarr: {e}")
            return False
    except Exception as e:
        logger.error(f"❌ Error adding movie to Radarr: {e}")
        return False


def add_series_to_sonarr(series_data, root_folder, quality_profile):
    """Add a series to Sonarr."""
    try:
        url = f"{SONARR_API_URL}/api/v3/series"
        params = {"apikey": SONARR_API_KEY}

        payload = {
            "title": series_data['title'],
            "year": series_data.get('year'),
            "tvdbId": series_data.get('tvdbId'),
            "qualityProfileId": quality_profile,
            "rootFolderPath": root_folder,
            "monitored": True,
            "seasonFolder": True,
            "addOptions": {
                "searchForMissingEpisodes": True
            }
        }

        response = requests.post(url, params=params, json=payload, timeout=15)
        response.raise_for_status()

        logger.info(f"✅ Added series to Sonarr: {series_data['title']} ({series_data.get('year')})")
        return True

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            logger.warning(f"⚠️  Series already exists in Sonarr: {series_data['title']}")
            return False
        else:
            logger.error(f"❌ Error adding series to Sonarr: {e}")
            return False
    except Exception as e:
        logger.error(f"❌ Error adding series to Sonarr: {e}")
        return False


def main():
    """Main function to add missing items."""

    # Items to add — replace these examples with your own titles
    # (or adapt main() to read titles from a file / CLI args)
    movies = [
        {"title": "Example Movie", "year": 2020},
    ]

    tv_shows = [
        {"title": "Example Show", "year": 2021},
    ]

    logger.info("=" * 80)
    logger.info("Starting to add missing items to Radarr and Sonarr")
    logger.info("=" * 80)

    # Get Radarr configuration
    radarr_root = get_radarr_root_folder()
    radarr_quality = get_radarr_quality_profile()

    if not radarr_root or not radarr_quality:
        logger.error("Failed to get Radarr configuration")
        return

    logger.info(f"Radarr Root Folder: {radarr_root}")
    logger.info(f"Radarr Quality Profile ID: {radarr_quality}")

    # Get Sonarr configuration
    sonarr_root = get_sonarr_root_folder()
    sonarr_quality = get_sonarr_quality_profile()

    if not sonarr_root or not sonarr_quality:
        logger.error("Failed to get Sonarr configuration")
        return

    logger.info(f"Sonarr Root Folder: {sonarr_root}")
    logger.info(f"Sonarr Quality Profile ID: {sonarr_quality}")
    logger.info("")

    # Add movies
    logger.info("=" * 80)
    logger.info("ADDING MOVIES TO RADARR")
    logger.info("=" * 80)

    movies_added = 0
    for movie in movies:
        logger.info(f"Searching for: {movie['title']} ({movie.get('year', 'N/A')})")
        movie_data = search_radarr_movie(movie['title'], movie.get('year'))

        if movie_data:
            if add_movie_to_radarr(movie_data, radarr_root, radarr_quality):
                movies_added += 1
        else:
            logger.warning(f"❌ Could not find movie: {movie['title']}")

        logger.info("")

    # Add TV shows
    logger.info("=" * 80)
    logger.info("ADDING TV SHOWS TO SONARR")
    logger.info("=" * 80)

    shows_added = 0
    for show in tv_shows:
        logger.info(f"Searching for: {show['title']} ({show.get('year', 'N/A')})")
        series_data = search_sonarr_series(show['title'], show.get('year'))

        if series_data:
            if add_series_to_sonarr(series_data, sonarr_root, sonarr_quality):
                shows_added += 1
        else:
            logger.warning(f"❌ Could not find series: {show['title']}")

        logger.info("")

    # Summary
    logger.info("=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Movies added: {movies_added}/{len(movies)}")
    logger.info(f"TV shows added: {shows_added}/{len(tv_shows)}")
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
