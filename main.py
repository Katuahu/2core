#!/usr/bin/env python3

import asyncio
import datetime
import itertools
import json
import logging
import os
import random
import string
import time
from ipaddress import ip_address
from typing import Dict, Optional
import sys

# Check Python version
if sys.version_info < (3, 7):
    print("This script requires Python 3.7 or higher")
    sys.exit(1)

try:
    import aiohttp
    import requests
    from telegram import Update
    from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes,
                            MessageHandler, filters)
except ImportError as e:
    print(f"Required package missing: {e}")
    print("Please install required packages using:")
    print("pip3 install -r requirements.txt")
    sys.exit(1)

from config import ADMIN_IDS, BOT_TOKEN, OWNER_USERNAME

# Configure logging with proper file permissions
log_dir = "logs"
if not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir, mode=0o755)
    except OSError as e:
        print(f"Could not create log directory: {e}")
        sys.exit(1)

log_file = os.path.join(log_dir, "bot.log")
try:
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[
            logging.FileHandler(log_file, mode='a'),
            logging.StreamHandler()
        ]
    )
except Exception as e:
    print(f"Could not initialize logging: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

# Ensure data directory exists with proper permissions
data_dir = "data"
if not os.path.exists(data_dir):
    try:
        os.makedirs(data_dir, mode=0o755)
    except OSError as e:
        logger.critical(f"Could not create data directory: {e}")
        sys.exit(1)

# Update file paths to use data directory
USER_FILE = os.path.join(data_dir, "users.json")
KEY_FILE = os.path.join(data_dir, "keys.json")

# Rest of your existing classes (UserManager, RateLimiter, AttackManager) remain the same
# ... (keep all the existing code)

async def main():
    try:
        # Check for write permissions in required directories
        for directory in [data_dir, log_dir]:
            if not os.access(directory, os.W_OK):
                logger.critical(f"No write permission in {directory}")
                sys.exit(1)

        # Initialize components
        user_manager = UserManager()
        attack_manager = AttackManager(user_manager)
        
        # Create application
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Register handlers
        register_handlers(app, user_manager, attack_manager)
        
        # Start cleanup task
        cleanup_task = asyncio.create_task(periodic_cleanup(attack_manager))
        
        try:
            # Start polling
            logger.info("Starting bot...")
            await app.initialize()
            await app.start()
            await app.run_polling(allowed_updates=Update.ALL_TYPES)
        finally:
            # Ensure proper cleanup
            logger.info("Stopping bot...")
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            await app.stop()
            
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}", exc_info=True)
        raise

def run_bot():
    """Run the bot with proper asyncio handling"""
    try:
        # Set proper umask for file creation
        os.umask(0o022)
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    run_bot()