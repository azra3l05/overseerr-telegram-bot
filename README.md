# 🎬 Overseerr Telegram Bot

A feature-rich Telegram bot for requesting movies and TV shows through Overseerr, with real-time availability notifications, smart library selection, and comprehensive admin controls.

## ✨ Features

### User Features
- **🔍 Smart Search**: Search for movies and TV shows with ratings, overviews, and year
- **📺 Library Selection**: Choose specific libraries for organized media collections
- **⚡ Instant Availability**: Automatically detects if content is already in your library
- **🔔 Real-time Notifications**: Get notified when requested content becomes available
- **📋 Request Management**: View, track, and cancel your requests
- **🌐 Browse Trending**: Discover popular movies and TV shows
- **📊 Request Status**: Check download/processing status of your requests

### Admin Features
- **📈 Enhanced Statistics**: View top users, libraries, popular requests, and trends
- **💾 Backup & Restore**: Manual and automatic daily database backups
- **🏥 Health Check**: HTTP endpoint for monitoring bot status
- **🔄 Retry Logic**: Automatic retry on API failures with exponential backoff

## 🏗️ Architecture

```
overseerr-telegram-bot/
├── telegram_overseerr_bot.py   # Main entry point (starts the bot)
├── config.py                   # Configuration & startup validation
├── database.py                 # Request persistence (JSON + optional Postgres)
├── handlers/                   # Telegram command & callback handlers (package)
│   ├── commands.py
│   ├── callbacks.py
│   └── utils.py
├── availability.py             # Background availability checker
├── notifications.py            # Notification delivery
├── overseerr_api.py            # Overseerr API client
├── radarr_sonarr_api.py        # Radarr/Sonarr API client
├── postgres_checker.py         # Direct Postgres availability checks
├── cache.py                    # Lightweight caching
├── rate_limiter.py             # API rate limiting
├── quality_upgrade_checker.py  # Detects quality upgrades
├── tag_manager.py              # Tags Radarr/Sonarr items by requesting user
├── dashboard.py                # Optional Flask web dashboard
├── integration_monitor.py      # Integration health monitoring
├── health_check.py             # Health HTTP endpoint
├── backup.py                   # Backup/restore of local data files
├── data_cleanup.py             # Periodic cleanup
├── sync_request_status.py      # Cron-style helper scripts
├── sync_radarr_sonarr_status.py
├── check_download_queue.py
├── add_missing_items.py
├── send_rejection_notification.py
├── utils.py                    # Shared utilities
├── sql/                        # PostgreSQL schema (apply if POSTGRES_ENABLED=true)
├── requirements.txt            # Python dependencies
├── .env.example                # Configuration template (copy to .env)
└── .gitignore
```

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- A Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Overseerr instance with API access
- Radarr and Sonarr instances (for real-time availability)
- PostgreSQL database (optional, for enhanced features)

### Installation

1. **Clone the repository**
```bash
git clone <your-repo-url>
cd overseerrbot_telegram
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Configure environment variables**
```bash
cp .env.example .env
nano .env  # Edit with your actual values
```

**⚠️ IMPORTANT**: Never commit `.env` file to version control!

4. **Set up systemd service** (optional)
```bash
sudo nano /etc/systemd/system/overseerr-telegram-bot.service
```

Add:
```ini
[Unit]
Description=Overseerr Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/overseerrbot_telegram
ExecStart=/usr/bin/python3 telegram_overseerr_bot.py
Restart=always
RestartSec=10
StandardOutput=append:/path/to/logs/telegram_bot_stdout.log
StandardError=append:/path/to/logs/telegram_bot_stderr.log

[Install]
WantedBy=multi-user.target
```

5. **Start the bot**
```bash
# Manual start
python3 telegram_overseerr_bot.py

# OR with systemd
sudo systemctl daemon-reload
sudo systemctl enable overseerr-telegram-bot
sudo systemctl start overseerr-telegram-bot
```

## ⚙️ Configuration

### Required Environment Variables

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token

# Overseerr
OVERSEERR_API_URL=https://overseerr.example.com/api/v1
OVERSEERR_API_KEY=your_api_key
TELEGRAMBOT_USERNAME=bot_user_email
TELEGRAMBOT_PASSWORD=bot_user_password

# Libraries (comma-separated Name:ID pairs)
LIBRARIES_MOVIES=🎬 Movies:1,🇺🇸 English:2
LIBRARIES_TV=📺 TV Shows:3,🇺🇸 English:4
```

### Optional Configuration

```bash
# TMDB (for enhanced search)
TMDB_API_KEY=your_tmdb_key

# Radarr/Sonarr (for real-time availability)
RADARR_API_URL=http://radarr:7878
RADARR_API_KEY=your_radarr_key
SONARR_API_URL=http://sonarr:8989
SONARR_API_KEY=your_sonarr_key

# PostgreSQL (for enhanced features)
POSTGRES_ENABLED=true
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DATABASE=overseerr
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_SCHEMA=public

# Health Check
HEALTH_CHECK_PORT=9090

# Admin (comma-separated Telegram user IDs)
ADMIN_USER_IDS=123456789,987654321
```

> **PostgreSQL setup**: if `POSTGRES_ENABLED=true`, create the tables first by
> applying the schema in [`sql/`](sql/) — e.g. `psql -d overseerr -f sql/create_requests_table.sql`,
> then the `add_*` migration files. Tables default to the `public` schema; to use a
> different one, set `POSTGRES_SCHEMA` and edit the schema name in the `sql/` files.

## 📝 Usage

### User Commands

- `/start` - Show welcome message and available commands
- `/searchmovie <title>` - Search for movies
- `/searchtv <title>` - Search for TV shows
- `/browse` - Browse trending movies
- `/browse tv` - Browse trending TV shows
- `/myrequests` - View all your requests with cancel option
- `/pending` - See requests waiting for availability
- `/status` - Check download/processing status

### Admin Commands

- `/stats` - View enhanced statistics dashboard
- `/backup` - Create manual database backup
- `/restore <backup_name>` - Restore from backup
- `/checknow` - Manually trigger availability check

### Health Check

Access the health check endpoint:
```bash
curl http://localhost:9090/health
```

Returns JSON:
```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "uptime_human": "0d 1h 0m",
  "last_activity_ago_seconds": 10,
  "total_requests": 150,
  "api_errors": 0,
  "timestamp": "2026-01-01T12:00:00"
}
```

## 🔧 Advanced Features

### Library Selection

The bot supports multi-library Overseerr setups. Users can select which library to add requested content to:
- Separate libraries for different languages
- Dedicated libraries for specific content types
- Emoji-based library names for better UX

### Real-time Availability Checking

Integration with Radarr/Sonarr APIs provides:
- Instant detection of already-available content
- Real-time file availability notifications
- Accurate status updates (downloading, processing, available)

### Automatic Backups

- Daily backups scheduled at 3:00 AM
- Keeps last 10 backups automatically
- Manual backup/restore via commands
- Backup location: `backups/backup_YYYYMMDD_HHMMSS/`

### Retry Logic

All API calls include automatic retry with exponential backoff:
- 3 retry attempts by default
- Exponential backoff (2^attempt seconds)
- Prevents transient network failures

## 🛡️ Security Best Practices

### Before Making Public

1. **Remove all sensitive data from code**
2. **Use `.env` for all secrets**
3. **Never commit `.env` to Git**
4. **Update `.gitignore`**:
   ```
   .env
   *.log
   __pycache__/
   backups/
   *.json
   *.db
   ```
5. **Sanitize commit history** if you've committed secrets
6. **Use environment variables** in deployment

### Recommended `.env.example`

Provide a template without real values:
```bash
# NEVER commit your actual .env file!
# Copy this to .env and fill in your values

TELEGRAM_BOT_TOKEN=
OVERSEERR_API_URL=
OVERSEERR_API_KEY=
TELEGRAMBOT_USERNAME=
TELEGRAMBOT_PASSWORD=
LIBRARIES_MOVIES=
LIBRARIES_TV=
```

## 🐛 Troubleshooting

### Bot not starting
```bash
# Check logs
tail -f /path/to/logs/telegram_bot_stderr.log

# Verify configuration
python3 -c "from config import *; print('Config loaded successfully')"
```

### API errors
- Verify Overseerr/Radarr/Sonarr URLs are accessible
- Check API keys are correct
- Ensure bot user exists in Overseerr

### Health check not accessible
- Check `HEALTH_CHECK_PORT` is not in use
- Verify firewall allows the port
- Test with: `curl http://localhost:9090/health`

## 📊 Database Structure

### requests_log.json
```json
{
  "id": 1,
  "user": "User Name (@username)",
  "title": "Movie Title (2024)",
  "type": "movie",
  "season": null,
  "library": "🎬 Movies",
  "timestamp": "2026-01-01 12:00:00",
  "tmdb_id": 12345,
  "overseerr_request_id": 67890
}
```

### availability_watch.json
```json
{
  "media_id": 12345,
  "media_type": "movie",
  "chat_id": 123456789,
  "title": "Movie Title",
  "library_name": "🎬 Movies",
  "season": null,
  "last_known_status": "checking",
  "confirmation_message_id": 9876
}
```

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 🙏 Acknowledgments

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API wrapper
- [Overseerr](https://github.com/sct/overseerr) - Media request management
- [Radarr](https://radarr.video/) - Movie collection manager
- [Sonarr](https://sonarr.tv/) - TV series manager

## 📞 Support

For issues, questions, or contributions, please open an issue on GitHub.

---

**⚠️ Disclaimer**: This bot is not affiliated with Overseerr, Radarr, or Sonarr. Use responsibly and ensure you have proper rights to access and download media content.
