#!/usr/bin/env python3
"""
Send rejection notification to Telegram user

Called by dashboard when a request is rejected.
"""

import os
import sys
import logging
import argparse
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from telegram import Bot
    from config import TELEGRAM_BOT_TOKEN
except ImportError as e:
    print(f"Error importing: {e}")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


async def send_rejection_message(chat_id: int, title: str, media_type: str, reason: str) -> bool:
    """
    Send rejection notification to user.

    Args:
        chat_id: Telegram user chat ID
        title: Title of the rejected request
        media_type: 'movie' or 'tv'
        reason: Rejection reason

    Returns:
        True if sent successfully, False otherwise
    """
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        # Format media type
        media_emoji = "🎬" if media_type == "movie" else "📺"
        media_label = "Movie" if media_type == "movie" else "TV Show"

        # Create message
        message = (
            f"❌ <b>Request Rejected</b>\n\n"
            f"{media_emoji} <b>{title}</b>\n"
            f"Type: {media_label}\n\n"
            f"<b>Reason:</b> {reason}\n\n"
            f"If you have questions about this rejection, please contact an admin."
        )

        # Send message (await the async call)
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='HTML'
        )

        logger.info(f"Rejection notification sent to chat_id {chat_id} for: {title}")
        return True

    except Exception as e:
        logger.error(f"Failed to send rejection notification: {e}")
        return False


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='Send rejection notification to Telegram user')
    parser.add_argument('chat_id', type=int, help='Telegram chat ID')
    parser.add_argument('title', type=str, help='Request title')
    parser.add_argument('media_type', type=str, choices=['movie', 'tv'], help='Media type')
    parser.add_argument('reason', type=str, help='Rejection reason')

    args = parser.parse_args()

    # Run the async function
    success = asyncio.run(send_rejection_message(args.chat_id, args.title, args.media_type, args.reason))

    if success:
        print("Notification sent successfully")
        sys.exit(0)
    else:
        print("Failed to send notification")
        sys.exit(1)


if __name__ == "__main__":
    main()
