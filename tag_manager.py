#!/usr/bin/env python3
"""
Tag Manager - Handles creation and retrieval of tags for Radarr/Sonarr
"""
import requests
import logging

logger = logging.getLogger(__name__)

# Tag mappings
BOT_TAGS = {
    "discord": {"radarr": 129, "sonarr": 98},
    "telegram": {"radarr": 130, "sonarr": 99}
}


def get_or_create_tag(service_url: str, api_key: str, tag_label: str) -> int:
    """
    Get tag ID by label, create if doesn't exist.

    Args:
        service_url: Radarr or Sonarr URL (e.g., http://radarr:7878)
        api_key: API key for the service
        tag_label: Tag label (e.g., "user-exampleuser")

    Returns:
        Tag ID
    """
    headers = {"X-Api-Key": api_key}

    # Get existing tags
    resp = requests.get(f"{service_url}/api/v3/tag", headers=headers, timeout=10)
    resp.raise_for_status()

    existing_tags = resp.json()

    # Check if tag exists
    for tag in existing_tags:
        if tag['label'].lower() == tag_label.lower():
            logger.info(f"Found existing tag '{tag_label}': {tag['id']}")
            return tag['id']

    # Create new tag
    logger.info(f"Creating new tag: {tag_label}")
    resp = requests.post(
        f"{service_url}/api/v3/tag",
        headers=headers,
        json={"label": tag_label},
        timeout=10
    )
    resp.raise_for_status()

    new_tag = resp.json()
    logger.info(f"Created tag '{tag_label}': {new_tag['id']}")
    return new_tag['id']


def get_user_tag(username: str, service_url: str, api_key: str) -> int:
    """
    Get or create user-specific tag.

    Args:
        username: Discord username (e.g., "exampleuser#0") or Telegram (@exampleuser)
        service_url: Radarr or Sonarr URL
        api_key: API key

    Returns:
        User tag ID
    """
    # Sanitize username for tag label
    # Remove special characters, spaces, convert to lowercase
    clean_username = username.lower()
    clean_username = clean_username.replace('#0', '').replace('@', '').replace('(', '').replace(')', '').replace(' ', '-')
    clean_username = clean_username.strip('-')  # Remove leading/trailing dashes

    tag_label = f"user-{clean_username}"
    return get_or_create_tag(service_url, api_key, tag_label)


def get_bot_and_user_tags(bot_type: str, username: str, service_url: str, api_key: str, service_type: str) -> list:
    """
    Get both bot-type tag and user-specific tag.

    Args:
        bot_type: "discord" or "telegram"
        username: Username
        service_url: Radarr or Sonarr URL
        api_key: API key
        service_type: "radarr" or "sonarr"

    Returns:
        List of tag IDs [bot_tag, user_tag]
    """
    # Bot type tag (predefined)
    bot_tag = BOT_TAGS[bot_type][service_type]

    # User tag (create if needed)
    user_tag = get_user_tag(username, service_url, api_key)

    return [bot_tag, user_tag]


if __name__ == "__main__":
    # Example usage — configure via environment variables (never hardcode keys)
    import os
    logging.basicConfig(level=logging.INFO)

    RADARR_URL = os.getenv("RADARR_API_URL", "http://radarr:7878")
    RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")

    tags = get_bot_and_user_tags(
        bot_type="discord",
        username="exampleuser#0",
        service_url=RADARR_URL,
        api_key=RADARR_API_KEY,
        service_type="radarr"
    )

    print(f"Tags for discord user exampleuser#0: {tags}")
