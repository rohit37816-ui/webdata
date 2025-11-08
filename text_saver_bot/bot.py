# text_saver_bot.py
# Requires: python-telegram-bot==20.7
# Install: pip install python-telegram-bot==20.7

import os
import json
import time
import zipfile
import tarfile
from io import BytesIO
from tempfile import NamedTemporaryFile
from shutil import move
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    constants,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = "7870355572:AAFHWAP5oLdwPg10jx5IGkZz6H_lMonuJL8"    # <-- replace with your BotFather token
OWNER_ID = 6065778458                 # <-- replace with your Telegram numeric user id (admin)
DATA_DIR = "data"
USERS_FILE = "users.json"
BACKUPS_DIR = "backups"
UPTIME_START = time.time()
SCHEMA_VERSION = 2                   # current schema version
# ----------------------------------------

# Ensure folders/files exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2)

# ---------- Safe JSON helpers ----------
def safe_write_json(path: str, obj):
    """Write JSON atomically: write to temp file then move."""
    dirn = os.path.dirname(path) or "."
    with NamedTemporaryFile("w", dir=dirn, delete=False, encoding="utf-8") as tf:
        json.dump(obj, tf, ensure_ascii=False, indent=2)
        tf.flush()
        try:
            os.fsync(tf.fileno())
        except:
            pass
        tmpname = tf.name
    move(tmpname, path)

def is_valid_json_file(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:
        return False

def startup_backup_and_check():
    """Create a pre-start backup and validate JSON files."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tarname = os.path.join(BACKUPS_DIR, f"prestart_{ts}.tar.gz")
    with tarfile.open(tarname, "w:gz") as tar:
        if os.path.exists(USERS_FILE):
            tar.add(USERS_FILE)
        if os.path.exists(DATA_DIR):
            tar.add(DATA_DIR)
    # validate JSONs
    bad = []
    if os.path.exists(USERS_FILE) and not is_valid_json_file(USERS_FILE):
        bad.append(USERS_FILE)
    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            p = os.path.join(root, f)
            if not is_valid_json_file(p):
                bad.append(p)
    if bad:
        raise RuntimeError(f"Startup JSON validation failed for: {bad}")

# ---------- Data helpers ----------
def load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    safe_write_json(USERS_FILE, users)

def user_data_path(user_id: int) -> str:
    return os.path.join(DATA_DIR, f"{user_id}.json")

def default_user_structure():
    return {
        "schema_version": SCHEMA_VERSION,
        "sections": [],
        "trash": [],
        "settings": {
            "theme": "light"   # or 'dark'
        }
    }

def ensure_user_data(user_id: int):
    path = user_data_path(user_id)
    if not os.path.exists(path):
        safe_write_json(path, default_user_structure())
    else:
        # run migration if needed
        migrate_user_file(path)
    return path

def load_user_data(user_id: int):
    ensure_user_data(user_id)
    with open(user_data_path(user_id), "r", encoding="utf-8") as f:
        return json.load(f)

def save_user_data(user_id: int, data):
    safe_write_json(user_data_path(user_id), data)

def is_logged_in(user_id: int) -> bool:
    users = load_users()
    return str(user_id) in users and users[str(user_id)].get("logged_in", False)

# ---------- Migration ----------
def migrate_user_file(path: str):
    """Migrate user file to current SCHEMA_VERSION if necessary."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # If invalid, replace with default structure (but keep backup)
        bak = path + ".corrupt.bak"
        move(path, bak)
        safe_write_json(path, default_user_structure())
        return

    ver = data.get("schema_version", 1)
    changed = False

    # Migrations example: v1 -> v2: add 'trash', 'settings', 'updated_at' fields
    if ver < 2:
        # ensure trash exists
        data.setdefault("trash", [])
        # ensure settings
        data.setdefault("settings", {"theme": "light"})
        # add updated_at to sections
        for s in data.get("sections", []):
            if "updated_at" not in s:
                s["updated_at"] = datetime.now(timezone.utc).isoformat()
            if "favorite" not in s:
                s["favorite"] = False
        data["schema_version"] = 2
        changed = True

    if changed:
        safe_write_json(path, data)

# ---------- Utilities ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def readable_iso(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str

def count_words(text: str) -> int:
    return len(text.strip().split())

# ---------- UI helpers ----------
def main_menu_markup(user_id: int):
    if is_logged_in(user_id):
        kb = [
            [InlineKeyboardButton("➕ Add", callback_data="menu_add"),
             InlineKeyboardButton("📂 Show", callback_data="menu_show")],
            [InlineKeyboardButton("✏️ Edit", callback_data="menu_edit"),
             InlineKeyboardButton("🗑 Delete", callback_data="menu_delete")],
            [InlineKeyboardButton("⭐ Favorites", callback_data="menu_fav"),
             InlineKeyboardButton("🗃 Export", callback_data="menu_export")],
            [InlineKeyboardButton("🗄 Backup", callback_data="menu_backup"),
             InlineKeyboardButton("🔎 Search", callback_data="menu_search")],
            [InlineKeyboardButton("🌓 Theme", callback_data="menu_theme"),
             InlineKeyboardButton("🗑️ Trash", callback_data="menu_trash")],
            [InlineKeyboardButton("📊 Stats", callback_data="menu_stats"),
             InlineKeyboardButton("🏓 Ping", callback_data="menu_ping")],
            [InlineKeyboardButton("🚪 Logout", callback_data="menu_logout")]
        ]
        text = "✅ *You are logged in!* Choose an action:"
    else:
        kb = [
            [InlineKeyboardButton("🔑 Login", callback_data="login_panel"),
             InlineKeyboardButton("🧾 Register", callback_data="register_panel")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")]
        ]
        text = "👋 *Welcome!* Please login or register to use the bot."
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    return text, InlineKeyboardMarkup(kb)

# ---------- Conversation states ----------
(
    ADD_TITLE, ADD_IMAGE, ADD_TEXT,
    EDIT_SELECT, EDIT_FIELD, EDIT_NEW,
    RESTORE_WAIT_FILE, SEARCH_QUERY
) = range(20)

# ---------- Command handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text, markup = main_menu_markup(user.id)
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=markup)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📘 *Commands & Quick Guide*\n\n"
        "/start - Open main menu\n"
        "/login - Login panel (buttons)\n"
        "/logout - Logout\n"
        "/add - Add section (title, image URL or skip, text)\n"
        "/show - Show your sections\n"
        "/edit - Edit a section\n"
        "/delete - Delete (move to Trash)\n"
        "/trash - View Trash (restore or permanently delete)\n"
        "/search - Search your sections\n"
        "/export - Export all text to .txt\n"
        "/backup - Download your data.json backup\n"
        "/restore - Upload a backup JSON to restore\n"
        "/stats - Show your stats (sections, favorites, words)\n"
        "/ping - Bot uptime\n"
        "/admin - Admin panel (admin only)\n\n"
        "🔹 All UI is button-driven. Use *🔙 Back* to return to menus.\n"
        "🔸 Passwords are stored locally (plain text). I can enable hashing if you want."
    )
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - UPTIME_START)
    h = uptime // 3600; m = (uptime % 3600) // 60; s = uptime % 60
    await update.message.reply_text(f"🏓 Uptime: {h}h {m}m {s}s")

# ---------- Login/Register ----------
async def login_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kb = [
        [InlineKeyboardButton("🔑 Login", callback_data="login_panel"),
         InlineKeyboardButton("🧾 Register", callback_data="register_panel")],
        [InlineKeyboardButton("🔙 Back", callback_data="btn_back")]
    ]
    await update.message.reply_text("👤 *Login Panel*\nChoose an option:", parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    # BACK
    if data == "btn_back":
        text, markup = main_menu_markup(user.id)
        try:
            await query.message.edit_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=markup)
        except:
            await query.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=markup)
        return

    # Login/Register panel
    if data == "login_panel":
        await query.message.edit_text("🔐 *Login*\nPlease send your password as a message (plain text).", parse_mode=constants.ParseMode.MARKDOWN)
        return
    if data == "register_panel":
        await query.message.edit_text("🧾 *Register*\nPlease send the password you'd like to use (plain text).", parse_mode=constants.ParseMode.MARKDOWN)
        return

    # Menu actions
    if data == "menu_add":
        await query.message.edit_text("➕ Use /add or tap Add to start adding a section.")
        return
    if data == "menu_show":
        return await show_sections_from_callback(query, user.id)
    if data == "menu_edit":
        await query.message.edit_text("✏️ Use /edit to start editing.")
        return
    if data == "menu_delete":
        await query.message.edit_text("🗑 Use /delete to remove (move to Trash).")
        return
    if data == "menu_fav":
        return await show_favorites_callback(query, user.id)
    if data == "menu_export":
        return await export_user_text(query, user.id)
    if data == "menu_backup":
        return await send_user_backup(query, user.id)
    if data == "menu_search":
        await query.message.edit_text("🔎 Use /search to find text inside your sections.")
        return
    if data == "menu_theme":
        return await toggle_theme_callback(query, user.id)
    if data == "menu_trash":
        return await show_trash_callback(query, user.id)
    if data == "menu_stats":
        return await stats_callback(query, user.id)
    if data == "menu_ping":
        uptime = int(time.time() - UPTIME_START); h = uptime // 3600; m = (uptime % 3600) // 60; s = uptime % 60
        await query.message.edit_text(f"🏓 Uptime: {h}h {m}m {s}s")
        return
    if data == "menu_logout":
        users = load_users()
        uid = str(user.id)
        if uid in users:
            users[uid]["logged_in"] = False
            save_users(users)
        await query.message.edit_text("🚪 You have been logged out. Use /login to log back in.")
        return

    # View section: view_{idx}
    if data.startswith("view_"):
        try:
            idx = int(data.split("_", 1)[1])
        except:
            await query.message.reply_text("⚠️ Invalid selection.")
            return
        return await display_section_callback(query, user.id, idx)

    # copy_{idx}
    if data.startswith("copy_"):
        idx = int(data.split("_", 1)[1])
        ud = load_user_data(user.id)
        sections = ud.get("sections", [])
        if 0 <= idx < len(sections):
            await query.message.reply_text(sections[idx]["text"])
        else:
            await query.message.reply_text("⚠️ Invalid section.")
        return

    # toggle_fav_{idx}
    if data.startswith("togglefav_"):
        idx = int(data.split("_", 1)[1])
        ud = load_user_data(user.id)
        if 0 <= idx < len(ud["sections"]):
            ud["sections"][idx]["favorite"] = not ud["sections"][idx].get("favorite", False)
            ud["sections"][idx]["updated_at"] = now_iso()
            save_user_data(user.id, ud)
            await query.message.edit_text(f"⭐ Favorite toggled for *{ud['sections'][idx]['title']}*.", parse_mode=constants.ParseMode.MARKDOWN)
        else:
            await query.message.reply_text("⚠️ Invalid section.")
        return

    # Trash view action: restore_{i} or purge_{i}
    if data.startswith("restore_"):
        idx = int(data.split("_", 1)[1])
        ud = load_user_data(user.id)
        if 0 <= idx < len(ud.get("trash", [])):
            item = ud["trash"].pop(idx)
            item["updated_at"] = now_iso()
            ud["sections"].append(item)
            save_user_data(user.id, ud)
            await query.message.edit_text(f"♻️ Restored *{item['title']}*.", parse_mode=constants.ParseMode.MARKDOWN)
        else:
            await query.message.reply_text("⚠️ Invalid item.")
        return
    if data.startswith("purge_"):
        idx = int(data.split("_", 1)[1])
        ud = load_user_data(user.id)
        if 0 <= idx < len(ud.get("trash", [])):
            item = ud["trash"].pop(idx)
            save_user_data(user.id, ud)
            await query.message.edit_text(f"🗑 Permanently deleted *{item['title']}*.", parse_mode=constants.ParseMode.MARKDOWN)
        else:
            await query.message.reply_text("⚠️ Invalid item.")
        return

    # Admin callbacks
    if data == "admin_panel":
        if user.id != OWNER_ID:
            await query.message.edit_text("⛔ Access denied.")
            return
        return await admin_panel_callback(query)
    if data == "admin_total":
        if user.id != OWNER_ID:
            await query.message.edit_text("⛔ Access denied.")
            return
        users = load_users()
        await query.message.edit_text(f"📊 Total registered users: *{len(users)}*", parse_mode=constants.ParseMode.MARKDOWN)
        return
    if data == "admin_list_users":
        if user.id != OWNER_ID:
            await query.message.edit_text("⛔ Access denied.")
            return
        users = load_users()
        if not users:
            await query.message.edit_text("📂 No registered users.")
            return
        kb = []
        for uid in users.keys():
            kb.append([InlineKeyboardButton(f"User {uid}", callback_data=f"admin_view_{uid}"),
                       InlineKeyboardButton("🗑 Delete", callback_data=f"admin_delete_{uid}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
        await query.message.edit_text("📁 Registered users:", reply_markup=InlineKeyboardMarkup(kb))
        return
    if data.startswith("admin_view_"):
        if user.id != OWNER_ID:
            await query.message.reply_text("⛔ Access denied.")
            return
        parts = data.split("_", 2)
        if len(parts) >= 3:
            target = parts[2]
            return await admin_view_user_callback(query, int(target))
    if data.startswith("admin_delete_"):
        if user.id != OWNER_ID:
            await query.message.reply_text("⛔ Access denied.")
            return
        tid = int(data.split("_", 2)[2])
        path = user_data_path(tid)
        if os.path.exists(path):
            os.remove(path)
        users = load_users()
        if str(tid) in users:
            del users[str(tid)]
            save_users(users)
        await query.message.edit_text(f"🗑 User {tid} data deleted.")
        return

    await query.message.reply_text("⚠️ Unknown action.")

# ---------- Message handler for login/register and generic ----------
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    user = update.effective_user
    uid = str(user.id)
    users = load_users()

    # If user exists and not logged_in -> treat as login attempt
    if uid in users and not users[uid].get("logged_in", False):
        if users[uid]["password"] == txt:
            users[uid]["logged_in"] = True
            save_users(users)
            ensure_user_data(user.id)
            await update.message.reply_text("✅ Login successful! Use /start to open the menu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back")]]))
        else:
            await update.message.reply_text("❌ Wrong password. Try again or register a new account.")
        return

    # If user not exists -> treat as registration password
    if uid not in users:
        users[uid] = {"password": txt, "logged_in": True, "settings": {"theme": "light"}}
        save_users(users)
        ensure_user_data(user.id)
        await update.message.reply_text("✅ Registered and logged in! Use /start to open the menu.")
        return

    # If logged in and sends a normal message, provide guidance
    await update.message.reply_text("ℹ️ Use the buttons or commands like /add, /show, /help.")

# ---------- Add flow ----------
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_logged_in(user.id):
        await update.message.reply_text("🔐 Please /login first.")
        return ConversationHandler.END
    await update.message.reply_text("➕ *Add Section* — Send the *title* for your section:", parse_mode=constants.ParseMode.MARKDOWN)
    return ADD_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_title"] = update.message.text.strip()
    await update.message.reply_text("📸 Send the *banner image URL* or type `skip` to omit:", parse_mode=constants.ParseMode.MARKDOWN)
    return ADD_IMAGE

async def add_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    context.user_data["new_image"] = None if txt.lower() == "skip" else txt
    await update.message.reply_text("💬 Now send the *text content* for this section:")
    return ADD_TEXT

async def add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text_content = update.message.text
    new_section = {
        "title": context.user_data.get("new_title", "Untitled"),
        "image": context.user_data.get("new_image"),
        "text": text_content,
        "favorite": False,
        "updated_at": now_iso()
    }
    ud = load_user_data(user.id)
    ud["sections"].append(new_section)
    save_user_data(user.id, ud)
    context.user_data.pop("new_title", None)
    context.user_data.pop("new_image", None)
    await update.message.reply_text("✅ Section saved! Use /show to view your sections.")
    return ConversationHandler.END

# ---------- Show flow ----------
async def show_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_logged_in(user.id):
        await update.message.reply_text("🔐 Please /login first.")
        return
    return await show_sections_from_message(update.message, user.id)

async def show_sections_from_message(message, user_id: int):
    ud = load_user_data(user_id)
    sections = ud.get("sections", [])
    if not sections:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back")]])
        await message.reply_text("📂 You have no sections yet. Use /add to create one.", reply_markup=kb)
        return
    kb = [[InlineKeyboardButton(f"{i+1}. {s['title']}", callback_data=f"view_{i}")] for i, s in enumerate(sections)]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await message.reply_text("📚 *Your Sections:*", parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def show_sections_from_callback(query, user_id: int):
    ud = load_user_data(user_id)
    sections = ud.get("sections", [])
    if not sections:
        await query.message.edit_text("📂 You have no sections yet.")
        return
    kb = [[InlineKeyboardButton(f"{i+1}. {s['title']}", callback_data=f"view_{i}")] for i, s in enumerate(sections)]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await query.message.edit_text("📚 *Your Sections:*", parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def display_section_callback(query, user_id: int, idx: int):
    ud = load_user_data(user_id)
    sections = ud.get("sections", [])
    if idx < 0 or idx >= len(sections):
        await query.message.reply_text("⚠️ Section not found.")
        return
    sec = sections[idx]
    updated = readable_iso(sec.get("updated_at", now_iso()))
    text = f"*{sec['title']}*\n\n{sec['text']}\n\n_Updated:_ {updated}"
    buttons = [
        [InlineKeyboardButton("📋 Copy Text", callback_data=f"copy_{idx}"),
         InlineKeyboardButton("⭐ Toggle Favorite", callback_data=f"togglefav_{idx}")],
        [InlineKeyboardButton("✏️ Edit", callback_data=f"editopen_{idx}"),
         InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{idx}")],
        [InlineKeyboardButton("🔙 Back", callback_data="btn_back")]
    ]
    # Try to edit if message exists otherwise send new
    try:
        # if image exists, send photo with caption
        if sec.get("image"):
            await query.message.delete()
            await query.message.reply_photo(photo=sec["image"], caption=text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.message.edit_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        if sec.get("image"):
            await query.message.reply_photo(photo=sec["image"], caption=text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))

# Handle editopen_{idx} and delete_{idx} callbacks
async def inline_edit_delete_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("editopen_"):
        idx = int(data.split("_", 1)[1])
        ud = load_user_data(user.id)
        if 0 <= idx < len(ud["sections"]):
            context.user_data["edit_index"] = idx
            kb = [
                [InlineKeyboardButton("📝 Edit Title", callback_data="edit_field_title"),
                 InlineKeyboardButton("🖼 Edit Image", callback_data="edit_field_image")],
                [InlineKeyboardButton("✒️ Edit Text", callback_data="edit_field_text")],
                [InlineKeyboardButton("🔙 Back", callback_data="btn_back")]
            ]
            await query.message.edit_text("✏️ Choose what to edit:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.reply_text("⚠️ Invalid section.")
        return

    if data.startswith("delete_"):
        idx = int(data.split("_", 1)[1])
        ud = load_user_data(user.id)
        if 0 <= idx < len(ud["sections"]):
            item = ud["sections"].pop(idx)
            item["deleted_at"] = now_iso()
            ud.setdefault("trash", []).append(item)
            save_user_data(user.id, ud)
            await query.message.edit_text(f"🗑 Moved *{item['title']}* to Trash.", parse_mode=constants.ParseMode.MARKDOWN)
        else:
            await query.message.reply_text("⚠️ Invalid section.")
        return

    # edit field choices
    if data.startswith("edit_field_"):
        field = data.split("_", 2)[2]
        context.user_data["which_edit"] = field
        if field == "title":
            await query.message.edit_text("📝 Send the new title (text).")
            context.user_data["awaiting_edit"] = True
            return
        if field == "image":
            await query.message.edit_text("🖼 Send new image URL or type `skip` to remove image.")
            context.user_data["awaiting_edit"] = True
            return
        if field == "text":
            await query.message.edit_text("✒️ Send the new text content.")
            context.user_data["awaiting_edit"] = True
            return

# ---------- Edit apply (message handler) ----------
async def edit_apply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_edit"):
        return
    user = update.effective_user
    idx = context.user_data.get("edit_index")
    which = context.user_data.get("which_edit")
    if idx is None or which is None:
        await update.message.reply_text("⚠️ No edit in progress.")
        context.user_data.pop("awaiting_edit", None)
        return ConversationHandler.END
    val = update.message.text.strip()
    ud = load_user_data(user.id)
    try:
        if which == "title":
            ud["sections"][idx]["title"] = val
        elif which == "image":
            ud["sections"][idx]["image"] = None if val.lower() == "skip" else val
        else:
            ud["sections"][idx]["text"] = val
        ud["sections"][idx]["updated_at"] = now_iso()
        save_user_data(user.id, ud)
        await update.message.reply_text("✅ Section updated.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")
    context.user_data.pop("edit_index", None)
    context.user_data.pop("which_edit", None)
    context.user_data.pop("awaiting_edit", None)
    return

# ---------- Delete via /delete (conversation not strictly needed) ----------
async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_logged_in(user.id):
        await update.message.reply_text("🔐 Please /login first.")
        return
    ud = load_user_data(user.id)
    sections = ud.get("sections", [])
    if not sections:
        await update.message.reply_text("📂 No sections to delete.")
        return
    kb = [[InlineKeyboardButton(f"{i+1}. {s['title']}", callback_data=f"delete_{i}")] for i, s in enumerate(sections)]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await update.message.reply_text("🗑 *Choose a section to delete:*", parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ---------- Trash ----------
async def show_trash_callback(query, user_id: int):
    ud = load_user_data(user_id)
    trash = ud.get("trash", [])
    if not trash:
        await query.message.edit_text("🗃 Your Trash is empty.")
        return
    kb = []
    for i, t in enumerate(trash):
        title = t.get("title", "Untitled")
        kb.append([InlineKeyboardButton(f"{i+1}. {title}", callback_data=f"trash_show_{i}")])
    # actions for each will be restore_{i} and purge_{i} via buttons
    # Show list with restore/purge buttons included below
    text_lines = []
    for i, t in enumerate(trash):
        deleted = readable_iso(t.get("deleted_at", now_iso()))
        text_lines.append(f"{i+1}. {t.get('title','Untitled')} — deleted {deleted}")
    text = "🗃 *Trash:* \n\n" + "\n".join(text_lines)
    # We'll add two rows: restore all? but here we present a simple view with numbered buttons for restore/purge
    actions = []
    for i in range(len(trash)):
        actions.append([InlineKeyboardButton(f"♻️ Restore {i+1}", callback_data=f"restore_{i}"),
                        InlineKeyboardButton(f"🗑 Purge {i+1}", callback_data=f"purge_{i}")])
    actions.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await query.message.edit_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(actions))

# ---------- Favorites ----------
async def show_favorites_callback(query, user_id: int):
    ud = load_user_data(user_id)
    favs = [ (i,s) for i,s in enumerate(ud.get("sections", [])) if s.get("favorite") ]
    if not favs:
        await query.message.edit_text("⭐ You have no favorite sections.")
        return
    kb = [[InlineKeyboardButton(f"{i+1}. {s['title']}", callback_data=f"view_{i}")] for i,s in favs]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await query.message.edit_text("⭐ *Favorites:*", parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ---------- Export / Backup / Restore ----------
async def export_user_text(trigger, user_id: int):
    ud = load_user_data(user_id)
    sections = ud.get("sections", [])
    if not sections:
        await trigger.message.reply_text("📂 No sections to export.")
        return
    buf = BytesIO()
    content = []
    for s in sections:
        content.append(f"== {s['title']} ==\nUpdated: {readable_iso(s.get('updated_at', now_iso()))}\n{s['text']}\n\n")
    buf.write("\n".join(content).encode("utf-8"))
    buf.seek(0)
    name = f"user_{user_id}_export.txt"
    await trigger.message.reply_document(document=InputFile(buf, filename=name), caption="📄 Exported text file")

async def send_user_backup(trigger, user_id: int):
    path = user_data_path(user_id)
    if not os.path.exists(path):
        await trigger.message.reply_text("📂 No data to backup.")
        return
    await trigger.message.reply_document(document=InputFile(path), filename=f"user_{user_id}_backup.json", caption="💾 Your data backup")

async def restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_logged_in(user.id):
        await update.message.reply_text("🔐 Please /login first.")
        return ConversationHandler.END
    await update.message.reply_text("📤 *Restore* — Send your backup JSON file as a document now.", parse_mode=constants.ParseMode.MARKDOWN)
    return RESTORE_WAIT_FILE

async def restore_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.document:
        await update.message.reply_text("⚠️ Please send a valid JSON file (as a document).")
        return RESTORE_WAIT_FILE
    file = await context.bot.get_file(update.message.document.file_id)
    bio = BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)
    try:
        data = json.load(bio)
        if "sections" not in data:
            raise ValueError("Invalid backup format.")
        # ensure schema_version and others
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("trash", [])
        data.setdefault("settings", {"theme": "light"})
        save_user_data(user.id, data)
        await update.message.reply_text("✅ Restore successful.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Restore failed: {e}")
    return ConversationHandler.END

# ---------- Search ----------
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_logged_in(user.id):
        await update.message.reply_text("🔐 Please /login first.")
        return ConversationHandler.END
    await update.message.reply_text("🔎 Send the keyword to search in your titles and text.")
    return SEARCH_QUERY

async def search_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip().lower()
    user = update.effective_user
    ud = load_user_data(user.id)
    found = []
    for i, s in enumerate(ud.get("sections", [])):
        if q in s.get("title", "").lower() or q in s.get("text", "").lower():
            found.append((i, s))
    if not found:
        await update.message.reply_text("🔍 No matches found.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"{i+1}. {s['title']}", callback_data=f"view_{i}")] for i, s in found]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await update.message.reply_text("🔍 Matches:", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ---------- Stats ----------
async def stats_callback(trigger, user_id: int):
    ud = load_user_data(user_id)
    sections = ud.get("sections", [])
    trash = ud.get("trash", [])
    total_words = sum(count_words(s.get("text","")) for s in sections)
    fav_count = sum(1 for s in sections if s.get("favorite"))
    txt = (f"📊 *Your Stats*\n\n"
           f"Sections: {len(sections)}\n"
           f"Favorites: {fav_count}\n"
           f"Trash items: {len(trash)}\n"
           f"Total words in sections: {total_words}")
    await trigger.message.edit_text(txt, parse_mode=constants.ParseMode.MARKDOWN)

# ---------- Theme toggle ----------
async def toggle_theme_callback(query, user_id: int):
    users = load_users()
    u = users.get(str(user_id), {})
    settings = u.setdefault("settings", {"theme":"light"})
    cur = settings.get("theme", "light")
    new = "dark" if cur == "light" else "light"
    settings["theme"] = new
    users[str(user_id)] = u
    save_users(users)
    await query.message.edit_text(f"🌓 Theme set to *{new}*.", parse_mode=constants.ParseMode.MARKDOWN)

# ---------- Admin ----------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("⛔ Access denied.")
        return
    kb = [
        [InlineKeyboardButton("📊 Total Users", callback_data="admin_total")],
        [InlineKeyboardButton("📁 List Users", callback_data="admin_list_users")],
        [InlineKeyboardButton("💾 Backup All (ZIP)", callback_data="admin_backup_all")],
        [InlineKeyboardButton("🔙 Back", callback_data="btn_back")]
    ]
    await update.message.reply_text("👑 *Admin Panel*", parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def admin_panel_callback(query):
    data = query.data
    user = query.from_user
    if user.id != OWNER_ID:
        await query.message.edit_text("⛔ Access denied.")
        return
    if data == "admin_backup_all":
        bytes_io = BytesIO()
        with zipfile.ZipFile(bytes_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(DATA_DIR):
                for f in files:
                    zf.write(os.path.join(root, f), arcname=os.path.join("data", f))
            zf.writestr("users.json", json.dumps(load_users()))
        bytes_io.seek(0)
        await query.message.reply_document(document=InputFile(bytes_io, filename="all_data_backup.zip"), caption="💾 All data backup")
        return

async def admin_view_user_callback(query, target_uid: int):
    path = user_data_path(target_uid)
    if not os.path.exists(path):
        await query.message.edit_text("📂 No data for that user.")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    sections = data.get("sections", [])
    if not sections:
        await query.message.edit_text("📂 User has no sections.")
        return
    kb = [[InlineKeyboardButton(f"{i+1}. {s['title']}", callback_data=f"admin_view_{target_uid}_{i}")] for i, s in enumerate(sections)]
    kb.append([InlineKeyboardButton("🗑 Delete User Data", callback_data=f"admin_delete_{target_uid}")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back")])
    await query.message.edit_text(f"📁 User {target_uid} sections:", reply_markup=InlineKeyboardMarkup(kb))

# ---------- Small helpers for handlers ----------
async def send_user_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_user_backup(update, update.effective_user.id)

async def export_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await export_user_text(update, update.effective_user.id)

async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    users = load_users()
    uid = str(user.id)
    if uid in users:
        users[uid]["logged_in"] = False
        save_users(users)
    await update.message.reply_text("🚪 Logged out. Use /login to log back in.")

# ---------- Registration/login via commands ----------
async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await login_panel_cmd(update, context)

# ---------- Startup & main ----------
def main():
    # startup backup & validation
    try:
        startup_backup_and_check()
    except Exception as e:
        print("Startup validation failed:", e)
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # basic commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("login", login_cmd))
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(CommandHandler("add", add_start))
    app.add_handler(CommandHandler("show", show_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("edit", lambda u,c: u.message.reply_text("✏️ Use the buttons from a section to edit (or /edit).")) )
    app.add_handler(CommandHandler("export", export_user_command))
    app.add_handler(CommandHandler("backup", send_user_backup_command))
    app.add_handler(CommandHandler("restore", restore_start))
    app.add_handler(CommandHandler("search", search_start))
    app.add_handler(CommandHandler("stats", lambda u,c: stats_callback(u, u.effective_user.id)))
    app.add_handler(CommandHandler("admin", admin_cmd))

    # Conversations: add
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_image)],
            ADD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_text)],
        },
        fallbacks=[],
    )
    app.add_handler(add_conv)

    # restore conv
    restore_conv = ConversationHandler(
        entry_points=[CommandHandler("restore", restore_start)],
        states={
            RESTORE_WAIT_FILE: [MessageHandler(filters.Document.ALL & ~filters.COMMAND, restore_receive_file)]
        },
        fallbacks=[],
    )
    app.add_handler(restore_conv)

    # search conv
    search_conv = ConversationHandler(
        entry_points=[CommandHandler("search", search_start)],
        states={
            SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_do)]
        },
        fallbacks=[],
    )
    app.add_handler(search_conv)

    # edit apply (listening for user's edit messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_message))

    # generic text handler (login/register)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    # callback router and inline edit/delete router share callback handler
    app.add_handler(CallbackQueryHandler(inline_edit_delete_router, pattern=r"^(editopen_|delete_|edit_field_)"))
    app.add_handler(CallbackQueryHandler(callback_router))

    print("✅ Bot started. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
