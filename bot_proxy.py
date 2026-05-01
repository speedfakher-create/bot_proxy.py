import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
from ipaddress import ip_address

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -----------------------------
# Configuration (هنا حطيت الـ Token والـ ID ديالك)
# -----------------------------
TOKEN = "8239511808:AAFkKOVC4r9w9VxfHBolFj_ENe32rdEARfc"
ADMIN_ID = 7210704553
PROXY_COMMAND = "mitmdump -s drageclint.py -p 5555 --set block_global=false"

DATA_DIR = Path(".")
ALLOWED_IPS_FILE = DATA_DIR / "allowed_ips.json"
STATS_FILE = DATA_DIR / "stats.json"
PROXY_PID_FILE = DATA_DIR / "proxy.pid"

DEFAULT_MINUTES = 60

# Conversation states
STATE_NONE = "none"
STATE_ADD_IP = "add_ip"
STATE_BULK_IMPORT = "bulk_import"
STATE_REMOVE_IP = "remove_ip"

# -----------------------------
# Storage & Logic (نفس الكود ديالك)
# -----------------------------
def ensure_data_dir(): DATA_DIR.mkdir(parents=True, exist_ok=True)

def read_json(path, default):
    ensure_data_dir()
    if not path.exists(): return default
    try:
        with path.open("r", encoding="utf-8") as f: return json.load(f)
    except: return default

def write_json(path, data):
    ensure_data_dir()
    with path.open("w", encoding="utf-8") as f: json.dump(data, f, indent=2)

def load_allowed_ips(): return read_json(ALLOWED_IPS_FILE, {})
def save_allowed_ips(data): write_json(ALLOWED_IPS_FILE, data)
def load_stats(): return read_json(STATS_FILE, {"requests": 0, "blocked": 0, "allowed": 0, "top_ips": {}})

# --- Proxy Management ---
def proxy_running():
    if not PROXY_PID_FILE.exists(): return False
    try:
        pid = int(PROXY_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except: return False

def start_proxy_process():
    if proxy_running(): return "▶️ البروكسي خدام ديجا."
    proc = subprocess.Popen(PROXY_COMMAND, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    PROXY_PID_FILE.write_text(str(proc.pid))
    return f"✅ تطلق البروكسي (PID: {proc.pid})"

def stop_proxy_process():
    if proxy_running():
        pid = int(PROXY_PID_FILE.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        PROXY_PID_FILE.unlink()
        return "⏹ تطفى البروكسي."
    return "⏹ البروكسي طافي ديجا."

# --- Telegram Handlers (نفس منطق الكود ديالك) ---
async def start(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data["state"] = STATE_NONE
    await update.message.reply_html("🔐 <b>Proxy Admin Panel v3.0</b>", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start", callback_data="start_proxy"), InlineKeyboardButton("⏹ Stop", callback_data="stop_proxy")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats"), InlineKeyboardButton("📋 Active IPs", callback_data="list_ips")]
    ]))

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "start_proxy": await query.edit_message_text(start_proxy_process())
    elif query.data == "stop_proxy": await query.edit_message_text(stop_proxy_process())
    elif query.data == "list_ips": 
        ips = load_allowed_ips()
        await query.edit_message_text("📋 IPs: " + (", ".join(ips.keys()) if ips else "None"))
    elif query.data == "stats":
        stats = load_stats()
        await query.edit_message_text(f"📊 Stats:\nRequests: {stats['requests']}\nAllowed: {stats['allowed']}\nBlocked: {stats['blocked']}")

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("🚀 PRO BOT v3.0 Started...")
    app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
