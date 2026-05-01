import json
import time
import os
import asyncio
import subprocess
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

TOKEN = "8239511808:AAFkKOVC4r9w9VxfHBolFj_ENe32rdEARfc"
ADMIN_ID = 7210704553

ALLOWED_IPS_FILE = "allowed_ips.txt"
STATS_FILE = "stats.json"

def load_allowed_ips():
    if os.path.exists(ALLOWED_IPS_FILE):
        with open(ALLOWED_IPS_FILE, 'r') as f:
            return {line.strip() for line in f if line.strip()}
    return set()

def save_allowed_ips(ips):
    with open(ALLOWED_IPS_FILE, 'w') as f:
        for ip in ips:
            f.write(f"{ip}\n")

def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    return {"requests": 0, "blocked": 0, "allowed": 0, "top_ips": {}}

def save_stats(stats):
    with open(STATS_FILE, 'w') as f:
        json.dump(stats, f, indent=2)

def proxy_running():
    try:
        output = subprocess.check_output(['pgrep', '-f', 'mitmdump'])
        return bool(output.strip())
    except subprocess.CalledProcessError:
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    status = "🟢 Running" if proxy_running() else "🔴 Stopped"
    stats = load_stats()
    
    keyboard = [
        [InlineKeyboardButton("📱 Add IP", callback_data="add_ip")],
        [InlineKeyboardButton("📋 IP List", callback_data="list_ips")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats")],
        [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_all")],
        [InlineKeyboardButton("▶️ Start Proxy", callback_data="start_proxy")],
        [InlineKeyboardButton("⏹️ Stop Proxy", callback_data="stop_proxy")],
        [InlineKeyboardButton("💾 Bulk Import", callback_data="bulk_import")],
        [InlineKeyboardButton("🎁 Giveaway", callback_data="giveaway")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = f"🔐 *PROXY ADMIN v2.0*\nProxy: {status}\nRequests: {stats.get('requests', 0)}\nBlocked: {stats.get('blocked', 0)}\nAllowed: {stats.get('allowed', 0)}"
    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    if query.data == "add_ip":
        await query.edit_message_text("📱 Send IP (format: `IP:minutes` or just `IP` for 60min)")
    elif query.data == "list_ips":
        ips = load_allowed_ips()
        if not ips:
            await query.edit_message_text("📋 No IPs whitelisted")
        else:
            display_ips = []
            for ip_entry in sorted(ips)[-10:]:
                if ':' in ip_entry:
                    ip, exp = ip_entry.rsplit(':', 1)
                    remaining = max(0, int((int(exp) - time.time()) / 60))
                    display_ips.append(f"• {ip} ({remaining} mins left)")
                else:
                    display_ips.append(f"• {ip_entry}")
            msg = "📋 *Active IPs:*\n" + "\n".join(display_ips)
            await query.edit_message_text(msg, parse_mode='Markdown')
    elif query.data == "stats":
        stats = load_stats()
        msg = f"📊 *STATS*\nTotal Requests: {stats.get('requests', 0)}\nBlocked: {stats.get('blocked', 0)}\nAllowed: {stats.get('allowed', 0)}"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif query.data == "clear_all":
        save_allowed_ips(set())
        await query.edit_message_text("🗑️ *Cleared everything*", parse_mode='Markdown')
    elif query.data == "start_proxy":
        os.system("nohup mitmdump -s drageclint.py -p 5555 --set block_global=false > nohup_proxy.out 2>&1 &")
        await query.edit_message_text("▶️ Starting proxy...")
    elif query.data == "stop_proxy":
        os.system("pkill -f mitmdump")
        await query.edit_message_text("⏹️ Stopping proxy...")
    elif query.data == "bulk_import":
        await query.edit_message_text("💾 Send IPs (one per line)")
    elif query.data == "giveaway":
        await query.edit_message_text("🎁 *Giveaway Link:*\n`185.237.15.10:5555/giveaway`\n\nShare this!", parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = update.message.text.strip()
    try:
        lines = text.split('\n')
        added = 0
        ips = load_allowed_ips()
        for line in lines:
            line = line.strip()
            if ':' in line and line.count(':') == 1:
                ip, minutes = line.split(':', 1)
                minutes = int(minutes) if minutes.isdigit() else 60
            else:
                ip = line
                minutes = 60
            if ip:
                exp_timestamp = int(time.time()) + (minutes * 60)
                ips.add(f"{ip}:{exp_timestamp}")
                added += 1
        save_allowed_ips(ips)
        await update.message.reply_text(f"✅ Added {added} IPs")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    ips = load_allowed_ips()
    now = int(time.time())
    cleaned = set()
    for ip_entry in list(ips):
        if ':' in ip_entry:
            ip, exp_timestamp = ip_entry.rsplit(':', 1)
            if now < int(exp_timestamp):
                cleaned.add(ip_entry)
        else:
            cleaned.add(ip_entry)
    if len(cleaned) < len(ips):
        save_allowed_ips(cleaned)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(cleanup_job, interval=300, first=10)
    app.run_polling()
