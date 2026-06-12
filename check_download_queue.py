#!/usr/bin/env python3
"""
Check Radarr and Sonarr download queues

Shows which items are currently downloading or queued.
"""

import os
import sys
import requests
import logging
from typing import List, Dict

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import RADARR_API_URL, RADARR_API_KEY, SONARR_API_URL, SONARR_API_KEY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)


def get_radarr_queue() -> List[Dict]:
    """Get current Radarr download queue."""
    try:
        url = f"{RADARR_API_URL}/api/v3/queue"
        params = {"apikey": RADARR_API_KEY, "pageSize": 100}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        return data.get('records', [])

    except Exception as e:
        logger.error(f"Error getting Radarr queue: {e}")
        return []


def get_sonarr_queue() -> List[Dict]:
    """Get current Sonarr download queue."""
    try:
        url = f"{SONARR_API_URL}/api/v3/queue"
        params = {"apikey": SONARR_API_KEY, "pageSize": 100}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        return data.get('records', [])

    except Exception as e:
        logger.error(f"Error getting Sonarr queue: {e}")
        return []


def get_radarr_activity() -> List[Dict]:
    """Get movies being searched/monitored."""
    try:
        url = f"{RADARR_API_URL}/api/v3/movie"
        params = {"apikey": RADARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        movies = response.json()

        # Filter for monitored movies without files
        pending = []
        for movie in movies:
            if movie.get('monitored') and not movie.get('hasFile'):
                pending.append({
                    'title': movie.get('title'),
                    'year': movie.get('year'),
                    'status': movie.get('status', 'unknown')
                })

        return pending

    except Exception as e:
        logger.error(f"Error getting Radarr activity: {e}")
        return []


def get_sonarr_activity() -> List[Dict]:
    """Get series being searched/monitored."""
    try:
        url = f"{SONARR_API_URL}/api/v3/series"
        params = {"apikey": SONARR_API_KEY}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        series_list = response.json()

        # Filter for monitored series
        pending = []
        for series in series_list:
            if series.get('monitored'):
                stats = series.get('statistics', {})
                total_episodes = stats.get('episodeCount', 0)
                available_episodes = stats.get('episodeFileCount', 0)

                if available_episodes < total_episodes:
                    pending.append({
                        'title': series.get('title'),
                        'year': series.get('year'),
                        'total_episodes': total_episodes,
                        'available_episodes': available_episodes,
                        'missing_episodes': total_episodes - available_episodes
                    })

        return pending

    except Exception as e:
        logger.error(f"Error getting Sonarr activity: {e}")
        return []


def format_size(bytes_size):
    """Format bytes to human readable size."""
    if not bytes_size:
        return "N/A"

    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0


def format_time(minutes):
    """Format minutes to human readable time."""
    if not minutes or minutes == 0:
        return "N/A"

    hours = int(minutes // 60)
    mins = int(minutes % 60)

    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def main():
    """Main function."""

    logger.info("=" * 100)
    logger.info("RADARR & SONARR DOWNLOAD STATUS")
    logger.info("=" * 100)
    logger.info("")

    # Get Radarr queue
    logger.info("🎬 RADARR - ACTIVE DOWNLOADS")
    logger.info("-" * 100)

    radarr_queue = get_radarr_queue()

    if radarr_queue:
        for i, item in enumerate(radarr_queue, 1):
            title = item.get('title', 'Unknown')
            status = item.get('status', 'unknown')
            progress = item.get('sizeleft', 0)
            total = item.get('size', 0)
            eta = item.get('timeleft', '')

            percent = 0
            if total > 0:
                percent = ((total - progress) / total) * 100

            logger.info(f"{i}. {title}")
            logger.info(f"   Status: {status}")
            logger.info(f"   Progress: {percent:.1f}% ({format_size(total - progress)} / {format_size(total)})")
            if eta:
                logger.info(f"   ETA: {eta}")
            logger.info("")
    else:
        logger.info("   No active downloads")
        logger.info("")

    # Get Radarr monitored movies without files
    logger.info("🎬 RADARR - MONITORED (Searching for downloads)")
    logger.info("-" * 100)

    radarr_pending = get_radarr_activity()

    if radarr_pending:
        for i, movie in enumerate(radarr_pending, 1):
            logger.info(f"{i}. {movie['title']} ({movie.get('year', 'N/A')}) - Status: {movie['status']}")
        logger.info(f"\nTotal: {len(radarr_pending)} movies searching")
    else:
        logger.info("   No pending movies")

    logger.info("")
    logger.info("")

    # Get Sonarr queue
    logger.info("📺 SONARR - ACTIVE DOWNLOADS")
    logger.info("-" * 100)

    sonarr_queue = get_sonarr_queue()

    if sonarr_queue:
        for i, item in enumerate(sonarr_queue, 1):
            series = item.get('series', {})
            episode = item.get('episode', {})
            title = series.get('title', 'Unknown')
            season = episode.get('seasonNumber', 0)
            ep_num = episode.get('episodeNumber', 0)
            ep_title = episode.get('title', '')
            status = item.get('status', 'unknown')
            progress = item.get('sizeleft', 0)
            total = item.get('size', 0)
            eta = item.get('timeleft', '')

            percent = 0
            if total > 0:
                percent = ((total - progress) / total) * 100

            logger.info(f"{i}. {title} - S{season:02d}E{ep_num:02d}: {ep_title}")
            logger.info(f"   Status: {status}")
            logger.info(f"   Progress: {percent:.1f}% ({format_size(total - progress)} / {format_size(total)})")
            if eta:
                logger.info(f"   ETA: {eta}")
            logger.info("")
    else:
        logger.info("   No active downloads")
        logger.info("")

    # Get Sonarr monitored series
    logger.info("📺 SONARR - MONITORED (Searching for downloads)")
    logger.info("-" * 100)

    sonarr_pending = get_sonarr_activity()

    if sonarr_pending:
        for i, show in enumerate(sonarr_pending, 1):
            logger.info(f"{i}. {show['title']} ({show.get('year', 'N/A')})")
            logger.info(f"   Episodes: {show['available_episodes']}/{show['total_episodes']} available")
            logger.info(f"   Missing: {show['missing_episodes']} episodes")
            logger.info("")
        logger.info(f"Total: {len(sonarr_pending)} series with missing episodes")
    else:
        logger.info("   No pending series")

    logger.info("")
    logger.info("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
