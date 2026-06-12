# dashboard.py
"""
Web dashboard for Overseerr Telegram Bot.
Provides analytics, statistics, and monitoring interface.
Authentication handled by Authelia via Traefik reverse proxy.
"""
import os
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any, List
from flask import Flask, render_template, jsonify, request
import psycopg2

# Import bot modules
from database import (
    get_all_requests, get_user_stats, get_watchlist,
    get_pending_requests_by_priority
)
from cache import get_cache
from rate_limiter import get_rate_limiter
from config import (
    POSTGRES_ENABLED,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DATABASE,
    POSTGRES_USER,
    POSTGRES_PASSWORD
)

logger = logging.getLogger(__name__)

# Flask app configuration
app = Flask(__name__)

# Disable caching for development
@app.after_request
def add_header(response):
    response.cache_control.no_store = True
    response.cache_control.no_cache = True
    response.cache_control.must_revalidate = True
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Dashboard configuration
DASHBOARD_PORT = int(os.getenv('DASHBOARD_PORT', '8765'))
USE_AUTHELIA = os.getenv('USE_AUTHELIA', 'true').lower() == 'true'


# ============================================================================
# Helper: Get authenticated user from Authelia headers
# ============================================================================

def get_authenticated_user() -> str:
    """
    Get authenticated username from Authelia headers.
    Authelia passes authentication info via these headers:
    - Remote-User: username
    - Remote-Name: display name
    - Remote-Email: email
    - Remote-Groups: comma-separated groups
    """
    if USE_AUTHELIA:
        return request.headers.get('Remote-User', 'authenticated')
    return 'admin'  # Fallback if not using Authelia


# ============================================================================
# Dashboard Routes
# ============================================================================

@app.route('/')
def dashboard():
    """Main dashboard page."""
    try:
        # Get authenticated user from headers
        username = get_authenticated_user()
        logger.info(f"Dashboard accessed by: {username}")

        # Get statistics
        stats = get_dashboard_stats()
        stats['current_user'] = username
        return render_template('dashboard.html', stats=stats)
    except Exception as e:
        logger.exception("Dashboard error")
        return f"Error loading dashboard: {e}", 500


@app.route('/requests')
def requests_page():
    """Requests history page."""
    try:
        limit = request.args.get('limit', 50, type=int)
        requests_list = get_all_requests(limit=limit)
        return render_template('requests.html', requests=requests_list)
    except Exception as e:
        logger.exception("Requests page error")
        return f"Error loading requests: {e}", 500


@app.route('/users')
def users_page():
    """User statistics page."""
    try:
        users = get_user_stats(limit=50)
        return render_template('users.html', users=users)
    except Exception as e:
        logger.exception("Users page error")
        return f"Error loading users: {e}", 500


@app.route('/priority')
def priority_page():
    """Priority queue page."""
    try:
        requests_by_priority = get_pending_requests_by_priority(limit=100)
        return render_template('priority.html', requests_by_priority=requests_by_priority)
    except Exception as e:
        logger.exception("Priority page error")
        return f"Error loading priority queue: {e}", 500


@app.route('/watchlist')
def watchlist_page():
    """Watchlist monitoring page."""
    try:
        watchlist = get_watchlist()
        return render_template('watchlist.html', watchlist=watchlist)
    except Exception as e:
        logger.exception("Watchlist page error")
        return f"Error loading watchlist: {e}", 500


@app.route('/system')
def system_page():
    """System status page."""
    try:
        system_stats = get_system_stats()
        return render_template('system.html', stats=system_stats)
    except Exception as e:
        logger.exception("System page error")
        return f"Error loading system status: {e}", 500


# ============================================================================
# API Endpoints
# ============================================================================

@app.route('/api/stats')
def api_stats():
    """API endpoint for dashboard statistics."""
    try:
        stats = get_dashboard_stats()
        return jsonify(stats)
    except Exception as e:
        logger.exception("API stats error")
        return jsonify({'error': str(e)}), 500


@app.route('/api/requests/recent')
def api_recent_requests():
    """API endpoint for recent requests."""
    try:
        limit = request.args.get('limit', 10, type=int)
        requests_list = get_all_requests(limit=limit)
        return jsonify(requests_list)
    except Exception as e:
        logger.exception("API recent requests error")
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/status')
def api_watchlist_status():
    """API endpoint for watchlist status."""
    try:
        watchlist = get_watchlist()

        # Calculate statistics
        total = len(watchlist)
        by_type = {'movie': 0, 'tv': 0}
        by_status = {}

        for item in watchlist:
            media_type = item.get('media_type', 'unknown')
            if media_type in by_type:
                by_type[media_type] += 1

            status = item.get('last_known_status', 'checking')
            by_status[status] = by_status.get(status, 0) + 1

        return jsonify({
            'total': total,
            'by_type': by_type,
            'by_status': by_status
        })
    except Exception as e:
        logger.exception("API watchlist status error")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cache/stats')
def api_cache_stats():
    """API endpoint for cache statistics."""
    try:
        cache_stats = get_cache().get_stats()
        return jsonify(cache_stats)
    except Exception as e:
        logger.exception("API cache stats error")
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'authelia_enabled': USE_AUTHELIA
    })


@app.route('/api/requests/<int:request_id>/reject', methods=['POST'])
def reject_request(request_id):
    """Reject a request and notify the user."""
    try:
        data = request.json
        rejection_reason = data.get('reason', 'No reason provided')
        rejected_by = get_authenticated_user()

        if not POSTGRES_ENABLED:
            return jsonify({'error': 'PostgreSQL not enabled'}), 500

        # Connect to database
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DATABASE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        conn.autocommit = True  # prevent stale idle-in-transaction
        cursor = conn.cursor()

        # Get request details and chat info
        cursor.execute(
            "SELECT chat_id, title, media_type FROM public.telegram_requests WHERE id = %s",
            (request_id,)
        )
        result = cursor.fetchone()

        if not result:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Request not found'}), 404

        chat_id, title, media_type = result

        # Update database to mark as rejected
        cursor.execute(
            """
            UPDATE public.telegram_requests
            SET rejected = TRUE,
                rejection_reason = %s,
                rejected_at = NOW(),
                rejected_by = %s
            WHERE id = %s
            """,
            (rejection_reason, rejected_by, request_id)
        )
        conn.commit()
        cursor.close()
        conn.close()

        # Send notification to group chat via Telegram
        notification_sent = False
        if chat_id:
            try:
                result = subprocess.run(
                    [
                        'python3',
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'send_rejection_notification.py'),
                        str(chat_id),
                        title,
                        media_type,
                        rejection_reason
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                notification_sent = result.returncode == 0
                if not notification_sent:
                    logger.warning(f"Notification script failed: {result.stderr}")
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")

        return jsonify({
            'success': True,
            'message': 'Request rejected successfully',
            'notification_sent': notification_sent
        })

    except Exception as e:
        logger.exception("Reject request error")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Helper Functions
# ============================================================================

def get_dashboard_stats() -> Dict[str, Any]:
    """Get comprehensive dashboard statistics."""
    stats = {}

    # Request statistics
    all_requests = get_all_requests(limit=1000)
    stats['total_requests'] = len(all_requests)

    # Calculate requests by type
    movie_count = sum(1 for r in all_requests if r.get('type') == 'movie')
    tv_count = sum(1 for r in all_requests if r.get('type') == 'tv')
    stats['movie_requests'] = movie_count
    stats['tv_requests'] = tv_count

    # Recent requests (last 24 hours)
    now = datetime.now()
    recent_cutoff = now - timedelta(hours=24)
    recent_requests = []

    for r in all_requests:
        timestamp_str = r.get('timestamp', '')
        try:
            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            if timestamp > recent_cutoff:
                recent_requests.append(r)
        except:
            pass

    stats['requests_24h'] = len(recent_requests)

    # User statistics
    users = get_user_stats(limit=100)
    stats['total_users'] = len(users)
    stats['active_users'] = sum(1 for u in users if u.get('request_count', 0) > 0)

    # Top requesters
    top_users = sorted(users, key=lambda u: u.get('request_count', 0), reverse=True)[:5]
    stats['top_users'] = top_users

    # Watchlist statistics
    watchlist = get_watchlist()
    stats['watchlist_total'] = len(watchlist)
    stats['watchlist_movies'] = sum(1 for w in watchlist if w.get('media_type') == 'movie')
    stats['watchlist_tv'] = sum(1 for w in watchlist if w.get('media_type') == 'tv')

    # Priority queue statistics
    priority_requests = get_pending_requests_by_priority(limit=1000)
    stats['high_priority'] = len(priority_requests.get('high', []))
    stats['normal_priority'] = len(priority_requests.get('normal', []))
    stats['low_priority'] = len(priority_requests.get('low', []))

    # Cache statistics
    try:
        cache_stats = get_cache().get_stats()
        stats['cache'] = cache_stats
    except Exception as e:
        logger.warning(f"Could not get cache stats: {e}")
        stats['cache'] = {}

    # Rate limiter statistics
    try:
        rate_stats = get_rate_limiter().get_stats()
        stats['rate_limiter'] = rate_stats
    except Exception as e:
        logger.warning(f"Could not get rate limiter stats: {e}")
        stats['rate_limiter'] = {}

    # System status
    stats['postgres_enabled'] = POSTGRES_ENABLED
    stats['timestamp'] = now.strftime('%Y-%m-%d %H:%M:%S')

    return stats


def get_system_stats() -> Dict[str, Any]:
    """Get system status information."""
    stats = {}

    # Database status
    stats['postgres_enabled'] = POSTGRES_ENABLED

    # Cache status
    try:
        cache_stats = get_cache().get_stats()
        stats['cache_entries'] = cache_stats.get('entries', 0)
        stats['cache_hit_rate'] = cache_stats.get('hit_rate', '0%')
    except Exception as e:
        stats['cache_entries'] = 'Error'
        stats['cache_hit_rate'] = 'Error'
        logger.warning(f"Cache stats error: {e}")

    # Rate limiter status
    try:
        rate_stats = get_rate_limiter().get_stats()
        stats['rate_limiter_users'] = rate_stats.get('total_users_tracked', 0)
        stats['rate_limiter_calls'] = rate_stats.get('total_calls_tracked', 0)
    except Exception as e:
        stats['rate_limiter_users'] = 'Error'
        stats['rate_limiter_calls'] = 'Error'
        logger.warning(f"Rate limiter stats error: {e}")

    # Uptime (would need to track start time)
    stats['dashboard_version'] = '2.0.0'
    stats['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stats['authelia_enabled'] = USE_AUTHELIA

    return stats


# ============================================================================
# Run Dashboard
# ============================================================================

def start_dashboard(port: int = None):
    """Start the Flask dashboard server."""
    if port is None:
        port = DASHBOARD_PORT

    logger.info(f"Starting dashboard on port {port}")
    logger.info(f"Authelia authentication: {'enabled' if USE_AUTHELIA else 'disabled'}")
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    start_dashboard()
