import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Dict, List

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
# Configuration
# -----------------------------
# Never hardcode your bot token in the source code.
# Linux/macOS:
#   export TELEGRAM_BOT_TOKEN="123:abc"
#   export ADMIN_ID="7210704553"
# Optional proxy command:
#   export PROXY_COMMAND="python3 proxy_server.py"
TOKEN = os.getenv("8239511808:AAFkKOVC4r9w9VxfHBolFj_ENe32rdEARfc")
ADMIN_ID = int(os.getenv("7210704553", "0"))
PROXY_COMMAND = os.getenv("PROXY_COMMAND", "").strip()

DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "."))
ALLOWED_IPS_FILE = DATA_DIR / "allowed_ips.json"
STATS_FILE = DATA_DIR / "stats.json"
PROXY_PID_FILE = DATA_DIR / "proxy.pid"

DEFAULT_MINUTES = 60

# Conversation states stored per admin user
STATE_NONE = "none"
STATE_ADD_IP = "add_ip"
STATE_BULK_IMPORT = "bulk_import"
STATE_REMOVE_IP = "remove_ip"


# -----------------------------
# Storage helpers
# -----------------------------
def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    ensure_data_dir()
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    ensure_data_dir()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def load_allowed_ips() -> Dict[str, Dict[str, Any]]:
    """Returns {ip: {expires_at: unix_ts, minutes: int, created_at: unix_ts}}."""
    data = read_json(ALLOWED_IPS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_allowed_ips(data: Dict[str, Dict[str, Any]]) -> None:
    write_json(ALLOWED_IPS_FILE, data)


def load_stats() -> Dict[str, Any]:
    return read_json(
        STATS_FILE,
        {
            "requests": 0,
            "blocked": 0,
            "allowed": 0,
            "top_ips": {},
        },
    )


def save_stats(stats: Dict[str, Any]) -> None:
    write_json(STATS_FILE, stats)


# -----------------------------
# Validation and formatting
# -----------------------------
def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


def validate_ip(value: str) -> str:
    value = value.strip()
    # Allows IPv4 and IPv6. Raises ValueError if invalid.
    return str(ip_address(value))


def parse_ip_line(line: str) -> tuple[str, int]:
    """Accepts either 'IP' or 'IP:minutes'."""
    line = line.strip()
    if not line:
        raise ValueError("empty line")

    if ":" in line and line.count(":") == 1:
        raw_ip, raw_minutes = line.split(":", 1)
        minutes = int(raw_minutes.strip() or DEFAULT_MINUTES)
    else:
        raw_ip = line
        minutes = DEFAULT_MINUTES

    if minutes <= 0:
        raise ValueError("minutes must be greater than zero")

    return validate_ip(raw_ip), minutes


def human_time_left(expires_at: float) -> str:
    seconds = max(0, int(expires_at - time.time()))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def format_dashboard() -> str:
    stats = load_stats()
    ips = cleanup_expired_ips()
    proxy_status = "🟢 Running" if proxy_running() else "🔴 Stopped"

    return (
        "🔐 <b>Proxy Admin Panel</b>\n\n"
        f"<b>Status:</b> {proxy_status}\n"
        f"<b>Whitelisted IPs:</b> {len(ips)}\n\n"
        "📊 <b>Traffic</b>\n"
        f"• Total requests: <code>{stats.get('requests', 0)}</code>\n"
        f"• Allowed: <code>{stats.get('allowed', 0)}</code>\n"
        f"• Blocked: <code>{stats.get('blocked', 0)}</code>"
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add IP", callback_data="add_ip"),
                InlineKeyboardButton("📥 Bulk Import", callback_data="bulk_import"),
            ],
            [
                InlineKeyboardButton("📋 Active IPs", callback_data="list_ips"),
                InlineKeyboardButton("➖ Remove IP", callback_data="remove_ip"),
            ],
            [
                InlineKeyboardButton("📊 Stats", callback_data="stats"),
                InlineKeyboardButton("🧹 Clear Expired", callback_data="clear_expired"),
            ],
            [
                InlineKeyboardButton("▶️ Start Proxy", callback_data="start_proxy"),
                InlineKeyboardButton("⏹ Stop Proxy", callback_data="stop_proxy"),
            ],
            [
                InlineKeyboardButton("🗑 Clear All IPs", callback_data="clear_all_confirm"),
                InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
            ],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="refresh")]])


# -----------------------------
# IP management
# -----------------------------
def add_ip(ip: str, minutes: int) -> None:
    ips = load_allowed_ips()
    now = time.time()
    ips[ip] = {
        "minutes": minutes,
        "created_at": now,
        "expires_at": now + minutes * 60,
    }
    save_allowed_ips(ips)


def remove_ip(ip: str) -> bool:
    ips = load_allowed_ips()
    if ip not in ips:
        return False
    del ips[ip]
    save_allowed_ips(ips)
    return True


def cleanup_expired_ips() -> Dict[str, Dict[str, Any]]:
    ips = load_allowed_ips()
    now = time.time()
    active = {
        ip: meta
        for ip, meta in ips.items()
        if float(meta.get("expires_at", 0)) > now
    }
    if len(active) != len(ips):
        save_allowed_ips(active)
    return active


# -----------------------------
# Proxy process management
# -----------------------------
def read_proxy_pid() -> int | None:
    if not PROXY_PID_FILE.exists():
        return None
    try:
        return int(PROXY_PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def proxy_running() -> bool:
    pid = read_proxy_pid()
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_proxy_process() -> str:
    if proxy_running():
        return "▶️ Proxy is already running."

    if not PROXY_COMMAND:
        return (
            "⚠️ Proxy command is not configured.\n"
            "Set <code>PROXY_COMMAND</code> before using this button."
        )

    process = subprocess.Popen(
        PROXY_COMMAND,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PROXY_PID_FILE.write_text(str(process.pid), encoding="utf-8")
    return f"✅ Proxy started. PID: <code>{process.pid}</code>"


def stop_proxy_process() -> str:
    pid = read_proxy_pid()
    if not pid or not proxy_running():
        if PROXY_PID_FILE.exists():
            PROXY_PID_FILE.unlink()
        return "⏹ Proxy is already stopped."

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    if PROXY_PID_FILE.exists():
        PROXY_PID_FILE.unlink()
    return "✅ Proxy stopped."


# -----------------------------
# Telegram handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("❌ Unauthorized")
        return

    context.user_data["state"] = STATE_NONE
    await update.message.reply_html(format_dashboard(), reply_markup=main_menu())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_admin(update):
        await query.edit_message_text("❌ Unauthorized")
        return

    action = query.data
    context.user_data["state"] = STATE_NONE

    if action == "refresh":
        await query.edit_message_text(
            format_dashboard(),
            reply_markup=main_menu(),
            parse_mode=ParseMode.HTML,
        )

    elif action == "add_ip":
        context.user_data["state"] = STATE_ADD_IP
        await query.edit_message_text(
            "➕ <b>Add IP</b>\n\nSend one IP as:\n"
            "<code>1.2.3.4</code>\n\nOr with time in minutes:\n"
            "<code>1.2.3.4:120</code>",
            reply_markup=back_menu(),
            parse_mode=ParseMode.HTML,
        )

    elif action == "bulk_import":
        context.user_data["state"] = STATE_BULK_IMPORT
        await query.edit_message_text(
            "📥 <b>Bulk Import</b>\n\nSend multiple lines like:\n"
            "<code>1.2.3.4:60\n8.8.8.8:120\n9.9.9.9</code>",
            reply_markup=back_menu(),
            parse_mode=ParseMode.HTML,
        )

    elif action == "remove_ip":
        context.user_data["state"] = STATE_REMOVE_IP
        await query.edit_message_text(
            "➖ <b>Remove IP</b>\n\nSend the IP to remove, for example:\n"
            "<code>1.2.3.4</code>",
            reply_markup=back_menu(),
            parse_mode=ParseMode.HTML,
        )

    elif action == "list_ips":
        ips = cleanup_expired_ips()
        if not ips:
            message = "📋 No active whitelisted IPs."
        else:
            rows = []
            for ip, meta in sorted(ips.items()):
                rows.append(f"• <code>{ip}</code> — {human_time_left(float(meta['expires_at']))} left")
            message = "📋 <b>Active IPs</b>\n\n" + "\n".join(rows[:50])
        await query.edit_message_text(message, reply_markup=back_menu(), parse_mode=ParseMode.HTML)

    elif action == "stats":
        stats = load_stats()
        top_ips = stats.get("top_ips", {}) or {}
        top_lines = [f"• <code>{ip}</code>: {count}" for ip, count in sorted(top_ips.items(), key=lambda x: x[1], reverse=True)[:5]]
        message = (
            "📊 <b>Stats</b>\n\n"
            f"Total requests: <code>{stats.get('requests', 0)}</code>\n"
            f"Allowed: <code>{stats.get('allowed', 0)}</code>\n"
            f"Blocked: <code>{stats.get('blocked', 0)}</code>\n\n"
            "<b>Top IPs</b>\n"
            + ("\n".join(top_lines) if top_lines else "No IP traffic yet.")
        )
        await query.edit_message_text(message, reply_markup=back_menu(), parse_mode=ParseMode.HTML)

    elif action == "clear_expired":
        before = len(load_allowed_ips())
        after = len(cleanup_expired_ips())
        await query.edit_message_text(
            f"🧹 Removed <b>{before - after}</b> expired IPs.",
            reply_markup=back_menu(),
            parse_mode=ParseMode.HTML,
        )

    elif action == "clear_all_confirm":
        await query.edit_message_text(
            "⚠️ Are you sure you want to delete all whitelisted IPs?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Yes, clear all", callback_data="clear_all")],
                    [InlineKeyboardButton("⬅️ Cancel", callback_data="refresh")],
                ]
            ),
        )

    elif action == "clear_all":
        save_allowed_ips({})
        await query.edit_message_text("🗑 All whitelisted IPs were removed.", reply_markup=back_menu())

    elif action == "start_proxy":
        await query.edit_message_text(start_proxy_process(), reply_markup=back_menu(), parse_mode=ParseMode.HTML)

    elif action == "stop_proxy":
        await query.edit_message_text(stop_proxy_process(), reply_markup=back_menu(), parse_mode=ParseMode.HTML)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return

    text = (update.message.text or "").strip()
    state = context.user_data.get("state", STATE_NONE)

    if state == STATE_NONE:
        await update.message.reply_text("Use /start to open the admin panel.")
        return

    if state == STATE_ADD_IP:
        try:
            ip, minutes = parse_ip_line(text)
            add_ip(ip, minutes)
            context.user_data["state"] = STATE_NONE
            await update.message.reply_html(
                f"✅ Added <code>{ip}</code> for <b>{minutes}</b> minutes.",
                reply_markup=main_menu(),
            )
        except Exception as exc:
            await update.message.reply_html(f"❌ Invalid input: <code>{exc}</code>")
        return

    if state == STATE_BULK_IMPORT:
        added: List[str] = []
        errors: List[str] = []
        for index, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                ip, minutes = parse_ip_line(line)
                add_ip(ip, minutes)
                added.append(ip)
            except Exception as exc:
                errors.append(f"Line {index}: {exc}")

        context.user_data["state"] = STATE_NONE
        message = f"✅ Added <b>{len(added)}</b> IPs."
        if errors:
            message += "\n\n⚠️ <b>Skipped</b>\n" + "\n".join(f"• {e}" for e in errors[:10])
        await update.message.reply_html(message, reply_markup=main_menu())
        return

    if state == STATE_REMOVE_IP:
        try:
            ip = validate_ip(text)
            removed = remove_ip(ip)
            context.user_data["state"] = STATE_NONE
            if removed:
                await update.message.reply_html(f"✅ Removed <code>{ip}</code>.", reply_markup=main_menu())
            else:
                await update.message.reply_html(f"⚠️ <code>{ip}</code> was not in the list.", reply_markup=main_menu())
        except Exception as exc:
            await update.message.reply_html(f"❌ Invalid IP: <code>{exc}</code>")


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cleanup_expired_ips()


def validate_config() -> None:
    missing = []
    if not TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not ADMIN_ID:
        missing.append("ADMIN_ID")
    if missing:
        raise RuntimeError("Missing environment variable(s): " + ", ".join(missing))


def main() -> None:
    validate_config()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue:
        app.job_queue.run_repeating(cleanup_job, interval=300, first=10)

    print("Proxy Admin Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
