from telegram import Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes,
                         MessageHandler, filters)

from config import ADMIN_IDS, OWNER_USERNAME
from .constants import MAX_CONCURRENT_ATTACKS
from .managers import UserManager, RateLimiter, AttackManager

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

async def genkey(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                user_manager: UserManager, rate_limiter: RateLimiter) -> None:
    """Generate a new key (admin only)."""
    user_id = str(update.message.from_user.id)
    
    if not rate_limiter.can_execute(user_id):
        await update.message.reply_text("⚠️ Please wait before using commands again")
        return
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Only admins can generate keys")
        return

    try:
        if not user_manager.can_generate_keys(user_id):
            await update.message.reply_text(
                f"❌ Daily key generation limit reached ({MAX_KEYS_PER_ADMIN} keys)"
            )
            return

        usage = (
            "Usage:\n"
            "/genkey <amount> <hours/days> [custom_key]\n\n"
            "Examples:\n"
            "/genkey 24 hours\n"
            "/genkey 7 days\n"
            "/genkey 24 hours CUSTOM123"
        )

        if len(context.args) < 2:
            await update.message.reply_text(usage)
            return

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

        try:
            key = user_manager.generate_key(custom_key=custom_key)
        except ValueError as e:
            await update.message.reply_text(str(e))
            return

        user_manager.keys[key] = expiration_date
        user_manager.admin_key_counts[user_id] = user_manager.admin_key_counts.get(user_id, 0) + 1
        user_manager.save_data()

        await update.message.reply_text(
            f"🔑 Key generated:\nKey: `{key}`\n"
            f"Expires: {expiration_date} (IST)",
            parse_mode='Markdown'
        )
    except ValueError:
        await update.message.reply_text("Invalid number format")
    except Exception as e:
        logger.error(f"Error generating key: {e}")
        await update.message.reply_text("Error generating key")

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE,
                user_manager: UserManager, rate_limiter: RateLimiter) -> None:
    """Redeem a key."""
    user_id = str(update.message.from_user.id)
    
    if not rate_limiter.can_execute(user_id):
        await update.message.reply_text("⚠️ Please wait before using commands again")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /redeem <key>")
        return

    key = context.args[0]
    if key not in user_manager.keys:
        await update.message.reply_text("❌ Invalid or expired key")
        return

    expiration_date = user_manager.keys[key]
    user_manager.users[user_id] = {
        "expiration": expiration_date,
        "username": update.message.from_user.username or "Unknown",
        "joined_at": datetime.datetime.now(IST_TZ).isoformat()
    }
    
    del user_manager.keys[key]
    user_manager.save_data()

    await update.message.reply_text(
        f"✅ Key redeemed successfully!\n"
        f"Access granted until: {expiration_date} (IST)"
    )

async def bgmi(update: Update, context: ContextTypes.DEFAULT_TYPE,
              user_manager: UserManager, rate_limiter: RateLimiter,
              attack_manager: AttackManager) -> None:
    """Start a BGMI attack."""
    user_id = str(update.message.from_user.id)
    
    if not rate_limiter.can_execute(user_id):
        await update.message.reply_text("⚠️ Please wait before using commands again")
        return
    
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
              user_manager: UserManager, rate_limiter: RateLimiter,
              attack_manager: AttackManager) -> None:
    """Stop an active attack."""
    user_id = str(update.message.from_user.id)
    
    if not rate_limiter.can_execute(user_id):
        await update.message.reply_text("⚠️ Please wait before using commands again")
        return
    
    if not user_manager.is_user_authorized(user_id):
        await update.message.reply_text("❌ Access expired or unauthorized")
        return

    success, message = await attack_manager.stop_attack(user_id)
    await update.message.reply_text(message)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   rate_limiter: RateLimiter) -> None:
    """Broadcast a message to all users (admin only)."""
    user_id = str(update.message.from_user.id)
    
    if not rate_limiter.can_execute(user_id):
        await update.message.reply_text("⚠️ Please wait before using commands again")
        return
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Only admins can broadcast messages")
        return

    if not context.args:
        await update.message.reply_text('Usage: /broadcast <message>')
        return

    message = ' '.join(context.args)
    failed_users = []

    for user_id, user_data in user_manager.users.items():
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
    # Basic commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    
    # User management commands
    app.add_handler(CommandHandler("status", 
        lambda u, c: status(u, c, user_manager)))
    app.add_handler(CommandHandler("redeem", 
        lambda u, c: redeem(u, c, user_manager, rate_limiter)))
    
    # Attack commands
    app.add_handler(CommandHandler("bgmi", 
        lambda u, c: bgmi(u, c, user_manager, rate_limiter, attack_manager)))
    app.add_handler(CommandHandler("stop", 
        lambda u, c: stop(u, c, user_manager, rate_limiter, attack_manager)))
    
    # Admin commands
    app.add_handler(CommandHandler("genkey", 
        lambda u, c: genkey(u, c, user_manager, rate_limiter)))
    app.add_handler(CommandHandler("broadcast", 
        lambda u, c: broadcast(u, c, rate_limiter)))
    
    # Error handler
    app.add_error_handler(error_handler)