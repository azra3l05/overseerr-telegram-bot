# health_check.py
"""
Simple HTTP health check endpoint for monitoring bot status.
"""
import logging
import json
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

logger = logging.getLogger(__name__)


class BotHealthStatus:
    """Singleton to track bot health metrics."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.start_time = time.time()
            cls._instance.last_activity = time.time()
            cls._instance.total_requests = 0
            cls._instance.api_errors = 0
            cls._instance.is_healthy = True
        return cls._instance
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()
    
    def increment_requests(self):
        """Increment total request counter."""
        self.total_requests += 1
    
    def increment_errors(self):
        """Increment API error counter."""
        self.api_errors += 1
    
    def get_status(self) -> dict:
        """Get current health status."""
        uptime = time.time() - self.start_time
        last_activity_ago = time.time() - self.last_activity
        
        return {
            "status": "healthy" if self.is_healthy else "unhealthy",
            "uptime_seconds": int(uptime),
            "uptime_human": self._format_uptime(uptime),
            "last_activity_ago_seconds": int(last_activity_ago),
            "total_requests": self.total_requests,
            "api_errors": self.api_errors,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format uptime in human-readable format."""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{days}d {hours}h {minutes}m"


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check endpoint."""
    
    def do_GET(self):
        """Handle GET request."""
        if self.path == '/health' or self.path == '/':
            status = BotHealthStatus().get_status()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(status, indent=2).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default HTTP logging."""
        pass


def start_health_check_server(port: int = 8080):
    """Start health check HTTP server in background thread."""
    def run_server():
        try:
            server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
            logger.info(f"✅ Health check server started on port {port}")
            server.serve_forever()
        except Exception as e:
            logger.error(f"❌ Failed to start health check server: {e}")
    
    thread = Thread(target=run_server, daemon=True)
    thread.start()


def get_health_status():
    """Get singleton health status instance."""
    return BotHealthStatus()
