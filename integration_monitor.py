# integration_monitor.py
"""
Integration Health Monitoring
Monitors connectivity to external services and alerts on failures.
"""
import logging
import requests
from datetime import datetime
from typing import Dict, Tuple, Optional
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Track consecutive failures for each service
_failure_counts: Dict[str, int] = {
    "overseerr": 0,
    "radarr": 0,
    "sonarr": 0,
    "postgres": 0,
}

# Alert threshold (alert after N consecutive failures)
FAILURE_THRESHOLD = 3

# Discord webhook for alerts
DISCORD_ALERTS_WEBHOOK = None  # Will be set from config


def set_discord_webhook(webhook_url: str):
    """Set Discord webhook URL for alerts."""
    global DISCORD_ALERTS_WEBHOOK
    DISCORD_ALERTS_WEBHOOK = webhook_url


def check_overseerr() -> Tuple[bool, Optional[str]]:
    """
    Check Overseerr API connectivity.
    Returns: (is_healthy, error_message)
    """
    try:
        from config import OVERSEERR_API_URL, OVERSEERR_API_KEY

        url = f"{OVERSEERR_API_URL}/status"
        headers = {"X-Api-Key": OVERSEERR_API_KEY}

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return True, None
        else:
            return False, f"HTTP {response.status_code}"

    except Exception as e:
        return False, str(e)


def check_radarr() -> Tuple[bool, Optional[str]]:
    """
    Check Radarr API connectivity.
    Returns: (is_healthy, error_message)
    """
    try:
        from config import RADARR_API_URL, RADARR_API_KEY

        if not RADARR_API_URL or not RADARR_API_KEY:
            return True, None  # Not configured, skip check

        url = f"{RADARR_API_URL}/api/v3/system/status"
        headers = {"X-Api-Key": RADARR_API_KEY}

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return True, None
        else:
            return False, f"HTTP {response.status_code}"

    except Exception as e:
        return False, str(e)


def check_sonarr() -> Tuple[bool, Optional[str]]:
    """
    Check Sonarr API connectivity.
    Returns: (is_healthy, error_message)
    """
    try:
        from config import SONARR_API_URL, SONARR_API_KEY

        if not SONARR_API_URL or not SONARR_API_KEY:
            return True, None  # Not configured, skip check

        url = f"{SONARR_API_URL}/api/v3/system/status"
        headers = {"X-Api-Key": SONARR_API_KEY}

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return True, None
        else:
            return False, f"HTTP {response.status_code}"

    except Exception as e:
        return False, str(e)


def check_postgres() -> Tuple[bool, Optional[str]]:
    """
    Check PostgreSQL connectivity.
    Returns: (is_healthy, error_message)
    """
    try:
        from config import POSTGRES_ENABLED, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD

        if not POSTGRES_ENABLED:
            return True, None  # Not configured, skip check

        import psycopg2

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DATABASE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            connect_timeout=10
        )
        conn.autocommit = True  # prevent stale idle-in-transaction

        # Simple query to verify connection
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()

        return True, None

    except Exception as e:
        return False, str(e)


def send_discord_alert(service: str, error: str, failure_count: int):
    """Send Discord alert for service failure."""
    if not DISCORD_ALERTS_WEBHOOK:
        logger.warning("Discord webhook not configured, skipping alert")
        return

    try:
        embed = {
            "title": f"⚠️ {service.upper()} Connection Failed",
            "description": f"Service health check failed {failure_count} times consecutively.",
            "color": 16744272,  # Orange
            "fields": [
                {
                    "name": "Service",
                    "value": service.upper(),
                    "inline": True
                },
                {
                    "name": "Status",
                    "value": "❌ Unreachable",
                    "inline": True
                },
                {
                    "name": "Error",
                    "value": f"```{error[:500]}```",
                    "inline": False
                },
                {
                    "name": "Consecutive Failures",
                    "value": str(failure_count),
                    "inline": True
                }
            ],
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": "Overseerr Bot Integration Monitor"
            }
        }

        payload = {
            "username": "Integration Monitor",
            "embeds": [embed]
        }

        response = requests.post(DISCORD_ALERTS_WEBHOOK, json=payload, timeout=10)

        if response.status_code in (200, 204):
            logger.info(f"Sent Discord alert for {service} failure")
        else:
            logger.error(f"Failed to send Discord alert: HTTP {response.status_code}")

    except Exception as e:
        logger.exception(f"Error sending Discord alert: {e}")


def send_discord_recovery(service: str):
    """Send Discord notification when service recovers."""
    if not DISCORD_ALERTS_WEBHOOK:
        return

    try:
        embed = {
            "title": f"✅ {service.upper()} Connection Restored",
            "description": f"Service is now responding normally.",
            "color": 3066993,  # Green
            "fields": [
                {
                    "name": "Service",
                    "value": service.upper(),
                    "inline": True
                },
                {
                    "name": "Status",
                    "value": "✅ Healthy",
                    "inline": True
                }
            ],
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": "Overseerr Bot Integration Monitor"
            }
        }

        payload = {
            "username": "Integration Monitor",
            "embeds": [embed]
        }

        response = requests.post(DISCORD_ALERTS_WEBHOOK, json=payload, timeout=10)

        if response.status_code in (200, 204):
            logger.info(f"Sent Discord recovery notification for {service}")

    except Exception as e:
        logger.exception(f"Error sending Discord recovery: {e}")


async def health_check_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic health check job.
    Runs every 5 minutes, alerts on 3 consecutive failures.
    """
    global _failure_counts

    services = {
        "overseerr": check_overseerr,
        "radarr": check_radarr,
        "sonarr": check_sonarr,
        "postgres": check_postgres,
    }

    for service_name, check_func in services.items():
        try:
            is_healthy, error = check_func()

            if is_healthy:
                # Service is healthy
                if _failure_counts[service_name] >= FAILURE_THRESHOLD:
                    # Was failing, now recovered
                    logger.info(f"✅ {service_name} recovered after {_failure_counts[service_name]} failures")
                    send_discord_recovery(service_name)

                # Reset failure count
                _failure_counts[service_name] = 0

            else:
                # Service check failed
                _failure_counts[service_name] += 1

                logger.warning(
                    f"⚠️ {service_name} health check failed "
                    f"({_failure_counts[service_name]}/{FAILURE_THRESHOLD}): {error}"
                )

                # Alert on threshold
                if _failure_counts[service_name] == FAILURE_THRESHOLD:
                    logger.error(f"❌ {service_name} reached failure threshold, sending alert")
                    send_discord_alert(service_name, error, _failure_counts[service_name])

        except Exception as e:
            logger.exception(f"Error checking {service_name} health: {e}")


async def health_status_command(update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manual health check command.
    Usage: /health
    """
    try:
        services = {
            "Overseerr": check_overseerr,
            "Radarr": check_radarr,
            "Sonarr": check_sonarr,
            "PostgreSQL": check_postgres,
        }

        status_lines = ["*🏥 Integration Health Status*\n"]
        all_healthy = True

        for service_name, check_func in services.items():
            is_healthy, error = check_func()

            if is_healthy:
                status_lines.append(f"✅ *{service_name}*: Healthy")
            else:
                all_healthy = False
                status_lines.append(f"❌ *{service_name}*: Failed")
                if error:
                    status_lines.append(f"   └ _{error}_")

        if all_healthy:
            status_lines.append("\n🎉 All services operational!")
        else:
            status_lines.append("\n⚠️ Some services are experiencing issues.")

        message = "\n".join(status_lines)
        await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        logger.exception("health_status_command failed")
        await update.message.reply_text(f"❌ Health check failed: {e}")
