# notifications.py
"""
Advanced notification system for media availability updates.
Provides rich, detailed notifications with progress tracking and metadata.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class NotificationManager:
    """Manages advanced notifications with rich formatting and tracking."""

    def __init__(self):
        self.notification_history = {}  # Track sent notifications per user

    async def send_availability_notification(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        title: str,
        media_type: str,
        library_name: str,
        season: Optional[int] = None,
        episodes_available: int = 0,
        total_episodes: int = 0,
        quality: Optional[str] = None,
        size: Optional[str] = None,
        added_date: Optional[str] = None,
        confirmation_msg_id: Optional[int] = None
    ) -> bool:
        """
        Send rich availability notification with metadata.

        Args:
            context: Telegram context
            chat_id: User's chat ID
            title: Media title
            media_type: "movie" or "tv"
            library_name: Library name
            season: TV season number (optional)
            episodes_available: Number of available episodes
            total_episodes: Total episodes in season
            quality: Video quality (e.g., "1080p", "4K")
            size: File size (e.g., "4.5 GB")
            added_date: When it was added
            confirmation_msg_id: Message to delete after notification

        Returns:
            True if notification sent successfully
        """
        try:
            # Build rich notification message
            if media_type == "tv" and total_episodes > 0:
                # TV Show notification
                season_text = f" S{season}" if season else ""
                progress = f"{episodes_available}/{total_episodes}"

                if episodes_available >= total_episodes:
                    # Complete season
                    message_lines = [
                        f"✅ **{title}**{season_text}",
                        f"",
                        f"🎉 **Complete!** All {total_episodes} episodes are now available.",
                        f"📂 Library: {library_name}",
                    ]
                else:
                    # Partial availability
                    percentage = int((episodes_available / total_episodes) * 100)
                    message_lines = [
                        f"📺 **{title}**{season_text}",
                        f"",
                        f"🔄 **{progress} episodes** ({percentage}% complete)",
                        f"📂 Library: {library_name}",
                    ]
            else:
                # Movie notification
                message_lines = [
                    f"🎬 **{title}**",
                    f"",
                    f"✅ **Available!** Ready to watch now.",
                    f"📂 Library: {library_name}",
                ]

            # Add quality information if available
            if quality:
                message_lines.append(f"📊 Quality: {quality}")

            # Add size information if available
            if size:
                message_lines.append(f"💾 Size: {size}")

            # Add time since request if available
            if added_date:
                try:
                    added_dt = datetime.strptime(added_date, "%Y-%m-%d %H:%M:%S")
                    time_diff = datetime.now() - added_dt

                    if time_diff.days > 0:
                        wait_time = f"{time_diff.days} day{'s' if time_diff.days != 1 else ''}"
                    elif time_diff.seconds >= 3600:
                        hours = time_diff.seconds // 3600
                        wait_time = f"{hours} hour{'s' if hours != 1 else ''}"
                    else:
                        minutes = time_diff.seconds // 60
                        wait_time = f"{minutes} minute{'s' if minutes != 1 else ''}"

                    message_lines.append(f"⏱️ Wait time: {wait_time}")
                except Exception:
                    pass

            # Add helpful tip
            if media_type == "tv" and episodes_available < total_episodes:
                message_lines.append(f"")
                message_lines.append(f"💡 More episodes will be available soon!")
            else:
                message_lines.append(f"")
                message_lines.append(f"🍿 Enjoy watching!")

            message = "\n".join(message_lines)

            # Build inline keyboard with action buttons
            keyboard = []

            # Add "Open in Plex/Emby" button (if configured)
            # keyboard.append([InlineKeyboardButton("📺 Open in Plex", url="...")])

            # Add dismiss button
            keyboard.append([InlineKeyboardButton("✅ Got it!", callback_data="dismiss")])

            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

            # Send notification
            await context.bot.send_message(
                chat_id,
                message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )

            # Delete confirmation message if provided
            if confirmation_msg_id:
                try:
                    await context.bot.delete_message(chat_id, confirmation_msg_id)
                except Exception as e:
                    logger.warning(f"Could not delete confirmation message {confirmation_msg_id}: {e}")

            # Track notification
            if chat_id not in self.notification_history:
                self.notification_history[chat_id] = []
            self.notification_history[chat_id].append({
                "title": title,
                "media_type": media_type,
                "timestamp": datetime.now().isoformat(),
                "status": "delivered"
            })

            logger.info(f"✅ Sent availability notification for '{title}' to chat_id={chat_id}")
            return True

        except Exception as e:
            logger.exception(f"Failed to send availability notification: {e}")
            return False

    async def send_batch_notification(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        available_items: List[Dict[str, Any]]
    ) -> bool:
        """
        Send batch notification for multiple items that became available.

        Args:
            context: Telegram context
            chat_id: User's chat ID
            available_items: List of available items with metadata

        Returns:
            True if notification sent successfully
        """
        try:
            if not available_items:
                return False

            count = len(available_items)

            message_lines = [
                f"🎉 **{count} item{'s' if count != 1 else ''} now available!**",
                f""
            ]

            # List items (max 10 in batch notification)
            for item in available_items[:10]:
                title = item.get("title", "Unknown")
                media_type = item.get("media_type", "unknown")
                season = item.get("season")

                icon = "🎬" if media_type == "movie" else "📺"
                season_text = f" S{season}" if season else ""

                message_lines.append(f"{icon} {title}{season_text}")

            if count > 10:
                message_lines.append(f"... and {count - 10} more!")

            message_lines.append(f"")
            message_lines.append(f"🍿 Check your library and start watching!")

            message = "\n".join(message_lines)

            await context.bot.send_message(
                chat_id,
                message,
                parse_mode="Markdown"
            )

            logger.info(f"✅ Sent batch notification ({count} items) to chat_id={chat_id}")
            return True

        except Exception as e:
            logger.exception(f"Failed to send batch notification: {e}")
            return False

    async def send_progress_notification(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        title: str,
        status: str,
        progress: Optional[int] = None,
        eta: Optional[str] = None
    ) -> bool:
        """
        Send progress update notification.

        Args:
            context: Telegram context
            chat_id: User's chat ID
            title: Media title
            status: Status message (e.g., "Downloading", "Processing")
            progress: Progress percentage (0-100)
            eta: Estimated time of arrival

        Returns:
            True if notification sent successfully
        """
        try:
            message_lines = [f"📥 **{title}**", f""]

            # Add status with icon
            status_icons = {
                "downloading": "⬇️",
                "processing": "⚙️",
                "importing": "📂",
                "upgrading": "⬆️"
            }

            icon = status_icons.get(status.lower(), "🔄")
            message_lines.append(f"{icon} Status: **{status}**")

            # Add progress bar if percentage available
            if progress is not None and 0 <= progress <= 100:
                bar_length = 10
                filled = int((progress / 100) * bar_length)
                bar = "█" * filled + "░" * (bar_length - filled)
                message_lines.append(f"")
                message_lines.append(f"Progress: {bar} {progress}%")

            # Add ETA if available
            if eta:
                message_lines.append(f"⏱️ ETA: {eta}")

            message = "\n".join(message_lines)

            await context.bot.send_message(
                chat_id,
                message,
                parse_mode="Markdown"
            )

            logger.info(f"✅ Sent progress notification for '{title}' to chat_id={chat_id}")
            return True

        except Exception as e:
            logger.exception(f"Failed to send progress notification: {e}")
            return False

    async def send_failure_notification(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        title: str,
        error_message: str,
        retry_available: bool = True
    ) -> bool:
        """
        Send failure notification with retry option.

        Args:
            context: Telegram context
            chat_id: User's chat ID
            title: Media title
            error_message: Error description
            retry_available: Whether retry is available

        Returns:
            True if notification sent successfully
        """
        try:
            message_lines = [
                f"⚠️ **{title}**",
                f"",
                f"❌ **Failed:** {error_message}",
            ]

            if retry_available:
                message_lines.append(f"")
                message_lines.append(f"🔄 We'll try again automatically.")

            message = "\n".join(message_lines)

            await context.bot.send_message(
                chat_id,
                message,
                parse_mode="Markdown"
            )

            logger.info(f"✅ Sent failure notification for '{title}' to chat_id={chat_id}")
            return True

        except Exception as e:
            logger.exception(f"Failed to send failure notification: {e}")
            return False

    def get_notification_history(self, chat_id: int, limit: int = 10) -> List[Dict]:
        """
        Get notification history for a user.

        Args:
            chat_id: User's chat ID
            limit: Maximum number of notifications to return

        Returns:
            List of notification records
        """
        history = self.notification_history.get(chat_id, [])
        return history[-limit:]


# Global notification manager instance
_notification_manager = NotificationManager()


def get_notification_manager() -> NotificationManager:
    """Get the global notification manager instance."""
    return _notification_manager
