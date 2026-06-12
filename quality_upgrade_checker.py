# quality_upgrade_checker.py
"""
Quality upgrade notification system for Discord bot.
Checks if better quality versions are available for fulfilled requests.
"""
import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def get_media_quality_score(file_info: Dict) -> int:
    """
    Calculate quality score for a media file.

    Scoring system:
    - Resolution: 2160p=100, 1080p=80, 720p=60, 480p=40
    - Codec: H.265/HEVC=+20, H.264=+10
    - Audio: DTS-HD/TrueHD/Atmos=+15, DTS/DD+=+10, DD/AAC=+5
    - Source: Remux=+30, BluRay=+20, WEB-DL=+15, WEBRip=+10
    - Bitrate bonus: >20Mbps=+10, >10Mbps=+5

    Args:
        file_info: Dict with keys: resolution, codec, audio, source, bitrate_mbps, size_gb

    Returns:
        Quality score (0-200 range)
    """
    score = 0

    # Resolution score
    resolution = file_info.get("resolution", "").lower()
    if "2160p" in resolution or "4k" in resolution:
        score += 100
    elif "1080p" in resolution:
        score += 80
    elif "720p" in resolution:
        score += 60
    elif "480p" in resolution:
        score += 40

    # Video codec
    codec = file_info.get("codec", "").lower()
    if any(x in codec for x in ["h.265", "hevc", "x265"]):
        score += 20
    elif any(x in codec for x in ["h.264", "avc", "x264"]):
        score += 10

    # Audio codec
    audio = file_info.get("audio", "").lower()
    if any(x in audio for x in ["truehd", "dts-hd", "atmos"]):
        score += 15
    elif any(x in audio for x in ["dts", "dd+", "eac3"]):
        score += 10
    elif any(x in audio for x in ["dd", "ac3", "aac"]):
        score += 5

    # Source
    source = file_info.get("source", "").lower()
    if "remux" in source:
        score += 30
    elif "bluray" in source or "blu-ray" in source:
        score += 20
    elif "web-dl" in source or "webdl" in source:
        score += 15
    elif "webrip" in source:
        score += 10

    # Bitrate bonus
    bitrate = file_info.get("bitrate_mbps", 0)
    if bitrate > 20:
        score += 10
    elif bitrate > 10:
        score += 5

    return score


def extract_quality_from_radarr_sonarr(media_data: Dict) -> Optional[Dict]:
    """
    Extract quality info from Radarr/Sonarr API response.

    Args:
        media_data: Movie or episode data from Radarr/Sonarr API

    Returns:
        Dict with quality info or None
    """
    try:
        if not media_data.get("hasFile"):
            return None

        movie_file = media_data.get("movieFile") or media_data.get("episodeFile")
        if not movie_file:
            return None

        quality = movie_file.get("quality", {}).get("quality", {})
        media_info = movie_file.get("mediaInfo", {})

        quality_info = {
            "resolution": quality.get("resolution", "Unknown"),
            "codec": media_info.get("videoCodec", "Unknown"),
            "audio": media_info.get("audioCodec", "Unknown"),
            "source": quality.get("source", "Unknown"),
            "bitrate_mbps": media_info.get("videoBitrate", 0) / 1_000_000,  # Convert to Mbps
            "size_gb": movie_file.get("size", 0) / 1_073_741_824,  # Convert to GB
            "quality_name": quality.get("name", "Unknown"),
            "file_path": movie_file.get("relativePath", ""),
        }

        return quality_info

    except Exception as e:
        logger.error(f"Error extracting quality info: {e}")
        return None


async def check_quality_upgrades_job(context):
    """
    Periodic job to check for quality upgrades.
    Compares current quality against target quality profiles.
    Notifies users when significantly better quality is available.

    Should run less frequently than availability checker (e.g., once per day).
    """
    try:
        from database import get_watchlist, update_watchlist
        from radarr_sonarr_api import get_radarr_sonarr_api
        from overseerr_api import get_details

        api = get_radarr_sonarr_api()
        if not api:
            logger.warning("Radarr/Sonarr API not configured, skipping quality checks")
            return

        watchlist = get_watchlist()

        # Only check items marked as available and tracking upgrades
        trackable = [
            w for w in watchlist
            if w.get("last_known_status") == "complete_notified"
            and w.get("track_quality_upgrade", False)
            and not w.get("upgrade_notified", False)
        ]

        if not trackable:
            logger.info("No items to check for quality upgrades")
            return

        upgraded_count = 0

        for item in trackable:
            try:
                media_id = item.get("media_id")
                media_type = item.get("media_type")
                chat_id = item.get("chat_id")
                title = item.get("title", "Unknown")
                last_quality_score = item.get("quality_score", 0)

                # Get current quality from Radarr/Sonarr
                current_quality = None

                if media_type == "movie":
                    is_available, movie_data = api.check_movie_availability(media_id)
                    if is_available:
                        current_quality = extract_quality_from_radarr_sonarr(movie_data)

                elif media_type == "tv":
                    details = get_details(media_id, "tv")
                    tvdb_id = details.get("externalIds", {}).get("tvdbId")
                    if tvdb_id:
                        season = item.get("season")
                        _, is_full, _, _, show_data = api.check_tv_availability(tvdb_id, season_number=season)
                        if is_full:
                            current_quality = extract_quality_from_radarr_sonarr(show_data)

                if not current_quality:
                    continue

                # Calculate quality scores
                current_score = get_media_quality_score(current_quality)

                # Check if significant upgrade (20+ point improvement)
                improvement = current_score - last_quality_score

                if improvement >= 20:
                    # Send upgrade notification
                    quality_name = current_quality.get("quality_name", "Unknown")
                    resolution = current_quality.get("resolution", "")
                    codec = current_quality.get("codec", "")

                    season_text = f" S{item.get('season')}" if media_type == "tv" and item.get("season") else ""

                    message = (
                        f"⬆️ **Quality Upgrade Available!**\n\n"
                        f"**{title}**{season_text}\n"
                        f"New quality: {quality_name} ({resolution}, {codec})\n"
                        f"Quality score improved: {last_quality_score} → {current_score} (+{improvement})"
                    )

                    # Send via Discord bot
                    await context.bot.send_message(chat_id, message)

                    # Mark as notified
                    item["upgrade_notified"] = True
                    item["quality_score"] = current_score
                    item["last_upgrade_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    upgraded_count += 1
                    logger.info(f"Sent quality upgrade notification for {title} to user {chat_id}")

                else:
                    # Update score but don't notify
                    item["quality_score"] = current_score
                    item["last_upgrade_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            except Exception as e:
                logger.exception(f"Error checking quality for {item.get('title')}: {e}")
                continue

        # Update watchlist with new quality scores
        update_watchlist(watchlist)

        logger.info(f"Quality upgrade check complete: {upgraded_count} notifications sent")

    except Exception as e:
        logger.exception(f"Quality upgrade check failed: {e}")


def get_upgrade_recommendation(current_quality: Dict, target_profile: str = "high") -> Optional[str]:
    """
    Get upgrade recommendation based on current quality and target profile.

    Args:
        current_quality: Current quality info dict
        target_profile: "high" (1080p H.265) or "ultra" (4K H.265 Remux)

    Returns:
        Recommendation string or None if already optimal
    """
    current_score = get_media_quality_score(current_quality)

    if target_profile == "ultra":
        # Target: 4K H.265 Remux with Atmos (score ~175)
        if current_score >= 170:
            return None  # Already excellent
        return "Recommend: 4K H.265 Remux with Atmos audio"

    elif target_profile == "high":
        # Target: 1080p H.265 with good audio (score ~115)
        if current_score >= 110:
            return None  # Good enough

        resolution = current_quality.get("resolution", "").lower()
        codec = current_quality.get("codec", "").lower()

        recommendations = []

        if "720p" in resolution or "480p" in resolution:
            recommendations.append("Upgrade to 1080p")

        if "h.264" in codec or "x264" in codec:
            recommendations.append("Upgrade to H.265 for better compression")

        if recommendations:
            return "Recommend: " + ", ".join(recommendations)

    return None
