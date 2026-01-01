# backup.py
"""
Database backup and restore functionality.
"""
import os
import logging
import shutil
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Backup directory
BACKUP_DIR = Path("/home/azra3l/overseerrbot_telegram/backups")
BACKUP_DIR.mkdir(exist_ok=True)

# Files to backup
DATABASE_FILES = [
    "/home/azra3l/overseerrbot_telegram/data/requests.json",
    "/home/azra3l/overseerrbot_telegram/data/watchlist.json",
]


def create_backup() -> Optional[str]:
    """
    Create a backup of all database files.
    
    Returns:
        Path to backup directory, or None if failed
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"backup_{timestamp}"
        backup_path.mkdir()
        
        backup_info = {
            "timestamp": datetime.now().isoformat(),
            "files": []
        }
        
        for file_path in DATABASE_FILES:
            if os.path.exists(file_path):
                filename = os.path.basename(file_path)
                dest = backup_path / filename
                shutil.copy2(file_path, dest)
                backup_info["files"].append(filename)
                logger.info(f"Backed up {filename}")
        
        # Write backup metadata
        with open(backup_path / "backup_info.json", "w") as f:
            json.dump(backup_info, f, indent=2)
        
        logger.info(f"✅ Backup created: {backup_path}")
        return str(backup_path)
        
    except Exception as e:
        logger.exception(f"❌ Backup failed: {e}")
        return None


def restore_backup(backup_name: str) -> bool:
    """
    Restore database from a backup.
    
    Args:
        backup_name: Name of backup directory (e.g., "backup_20260101_120000")
        
    Returns:
        True if successful, False otherwise
    """
    try:
        backup_path = BACKUP_DIR / backup_name
        
        if not backup_path.exists():
            logger.error(f"Backup not found: {backup_path}")
            return False
        
        # Read backup info
        info_file = backup_path / "backup_info.json"
        if info_file.exists():
            with open(info_file) as f:
                backup_info = json.load(f)
                logger.info(f"Restoring backup from {backup_info.get('timestamp')}")
        
        # Restore each file
        for file_path in DATABASE_FILES:
            filename = os.path.basename(file_path)
            backup_file = backup_path / filename
            
            if backup_file.exists():
                # Create backup of current file before restoring
                if os.path.exists(file_path):
                    shutil.copy2(file_path, f"{file_path}.pre-restore")
                
                shutil.copy2(backup_file, file_path)
                logger.info(f"Restored {filename}")
        
        logger.info(f"✅ Restore completed from {backup_name}")
        return True
        
    except Exception as e:
        logger.exception(f"❌ Restore failed: {e}")
        return False


def list_backups() -> list:
    """
    List all available backups.
    
    Returns:
        List of backup directory names sorted by date (newest first)
    """
    try:
        backups = [d.name for d in BACKUP_DIR.iterdir() if d.is_dir() and d.name.startswith("backup_")]
        return sorted(backups, reverse=True)
    except Exception as e:
        logger.exception(f"Failed to list backups: {e}")
        return []


def cleanup_old_backups(keep_last: int = 10):
    """
    Remove old backups, keeping only the most recent ones.
    
    Args:
        keep_last: Number of backups to keep
    """
    try:
        backups = list_backups()
        
        if len(backups) <= keep_last:
            return
        
        to_delete = backups[keep_last:]
        
        for backup_name in to_delete:
            backup_path = BACKUP_DIR / backup_name
            shutil.rmtree(backup_path)
            logger.info(f"Deleted old backup: {backup_name}")
        
        logger.info(f"Cleaned up {len(to_delete)} old backup(s)")
        
    except Exception as e:
        logger.exception(f"Failed to cleanup backups: {e}")


async def scheduled_backup(context):
    """Job function for scheduled backups."""
    logger.info("Running scheduled backup...")
    backup_path = create_backup()
    
    if backup_path:
        # Cleanup old backups
        cleanup_old_backups(keep_last=10)
        logger.info("Scheduled backup completed successfully")
    else:
        logger.error("Scheduled backup failed")
