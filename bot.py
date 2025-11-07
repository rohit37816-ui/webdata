import os
import asyncio
import requests
import yt_dlp
import time
import random
from urllib.parse import parse_qs, urlparse, unquote
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread
from datetime import datetime

# === BOT Version ===
BOT_VERSION = "0.5"

# === Load BOT TOKEN from Environment ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN not found in environment variables!")
else:
    print(f"✅ BOT_TOKEN loaded successfully! Bot Version: {BOT_VERSION}")

# === Flask server (for uptime ping) ===
app = Flask(__name__)
@app.route('/')
def home():
    return f"✅ Bot v{BOT_VERSION} is alive and running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

# === Global variables ===
queue = []
is_downloading = False
rename_next_file = None
stats = {"files":0, "size":0, "total_speed":0}
current_task = None
start_time_bot = datetime.utcnow()

# === Spinner for animation ===
spinner_frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
spinner_index = 0

# === Progress bar helper ===
def make_progress_bar(percent, length=20):
    filled_length = int(length * percent / 100)
    bar = '█' * filled_length + '░' * (length - filled_length)
    return bar

async def update_progress_message(message, prefix, done_mb, total_mb, speed, eta):
    global spinner_index
    percent = (done_mb/total_mb)*100 if total_mb > 0 else 0
    bar = make_progress_bar(percent)
    spinner = spinner_frames[spinner_index % len(spinner_frames)]
    spinner_index += 1
    text = (
        f"{spinner} {prefix} {percent:.2f}%\n"
        f"[{bar}]\n"
        f"📥 {done_mb:.2f} MB / {total_mb:.2f} MB\n"
        f"⚡ Speed: {speed:.2f} MB/s\n"
        f"⏳ ETA: {eta:.1f}s"
    )
    await message.edit_text(text)

# === Extract file title ===
def extract_title(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "title" in query:
        return unquote(query["title"][0]).replace("|","-").replace(" ","_")
    filename = os.path.basename(parsed.path)
    if filename:
        return unquote(filename)
    return "file"

# === Download and Upload function ===
async def download_and_upload(update: Update, url):
    global is_downloading, stats, current_task, rename_next_file
    current_task = url
    is_downloading = True

    try:
        # Get filename
        filename = extract_title(url)
        ext = filename.split(".")[-1] if "." in filename else "mp4"
        if rename_next_file:
            filename = f"{rename_next_file}.{ext}"
            rename_next_file = None

        pinned_msg = await update.message.reply_text(f"🚀 Starting download: {filename}")
        start_time = time.time()

        # --- Download ---
        if "youtube.com" in url or "youtu.be" in url:
            ydl_opts = {'format': 'best', 'outtmpl': f'{filename}.%(ext)s'}
            loop = asyncio.get_event_loop()
            def run_yt_dlp():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            await loop.run_in_executor(None, run_yt_dlp)
            filename = f"{filename}.mp4" if not os.path.exists(filename) else filename

        else:
            headers = {"User-Agent": "Mozilla/5.0"}
            with requests.get(url, headers=headers, stream=True, timeout=(10, None)) as r:
                r.raise_for_status()
                total_length = int(r.headers.get('Content-Length',0))
                downloaded = 0
                chunk_size = 1024*1024
                last_update_time = time.time()
                with open(filename,'wb') as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if not is_downloading:
                            raise Exception("Download canceled")
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            elapsed = max(time.time()-start_time,0.1)
                            if time.time()-last_update_time>0.5:
                                speed = downloaded/(1024*1024)/elapsed
                                eta = (total_length-downloaded)/(1024*1024)/max(speed,0.001)
                                await update_progress_message(pinned_msg, "⬇️ Downloading", downloaded/(1024*1024), total_length/(1024*1024), speed, eta)
                                last_update_time = time.time()
                        await asyncio.sleep(random.uniform(0.05,0.1))

        # --- Update stats ---
        if os.path.exists(filename):
            file_size = os.path.getsize(filename)
            stats["files"] += 1
            stats["size"] += file_size
            stats["total_speed"] += (file_size/1024/1024)/max(time.time()-start_time,0.1)

        # --- Upload ---
        upload_start = time.time()
        total_size = os.path.getsize(filename)
        uploaded = 0
        chunk_size = 1024*1024*5
        while True:
            with open(filename,'rb') as f:
                data = f.read()
                if not data:
                    break
                uploaded += len(data)
                elapsed = max(time.time()-upload_start,0.1)
                speed = uploaded/(1024*1024)/elapsed
                eta = (total_size-uploaded)/(1024*1024)/max(speed,0.001)
                await update_progress_message(pinned_msg, "⬆️ Uploading", uploaded/(1024*1024), total_size/(1024*1024), speed, eta)
                break

        # Send the actual file
        with open(filename,'rb') as f:
            await update.message.reply_document(document=f, filename=filename)

        await pinned_msg.delete()
        os.remove(filename)

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        if os.path.exists(filename):
            os.remove(filename)
    finally:
        is_downloading = False

# === Queue processor ===
async def process_queue(update: Update):
    global is_downloading
    while queue:
        if not is_downloading:
            next_url = queue.pop(0)
            await download_and_upload(update, next_url)
        await asyncio.sleep(1)

# === Bot Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"✅ Bot v{BOT_VERSION} is alive! Use /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"🤖 *Downloader Bot v{BOT_VERSION} — Commands*\n\n"
        "/add <link> — Add link to queue\n"
        "/s or /startqueue — Start download queue\n"
        "/list — Show queued links\n"
        "/clear — Clear queue\n"
        "/status — Show current task\n"
        "/cancel — Cancel current download\n"
        "/stats — Show session stats\n"
        "/rename <new_name> — Rename next file\n"
        "/reset — Reset bot queue and stats\n"
        "/ping — Show uptime\n"
        "/help — Show this message"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: /add <link>")
        return
    for link in context.args:
        queue.append(link)
    await update.message.reply_text(f"✅ Added {len(context.args)} link(s). Queue size: {len(queue)}")

async def list_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not queue:
        await update.message.reply_text("📭 Queue is empty.")
    else:
        text = "\n".join([f"{i+1}. {extract_title(item)}" for i,item in enumerate(queue)])
        await update.message.reply_text(f"📦 *Download Queue:*\n{text}", parse_mode=ParseMode.MARKDOWN)

async def clear_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    queue.clear()
    await update.message.reply_text("🗑️ Queue cleared!")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_downloading:
        await update.message.reply_text(f"⏳ Currently downloading:\n{current_task}")
    else:
        await update.message.reply_text("✅ Idle. No active downloads.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_downloading
    if is_downloading:
        is_downloading = False
        await update.message.reply_text("🛑 Current task canceled.")
    else:
        await update.message.reply_text("⚠️ No active task to cancel.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = stats["files"]
    size = stats["size"]
    avg_speed = (stats["total_speed"]/files) if files>0 else 0
    text = (f"📊 *Session Stats*\n"
            f"📁 Files downloaded: {files}\n"
            f"💾 Total size: {size/1024/1024:.2f} MB\n"
            f"⚡ Avg speed: {avg_speed:.2f} MB/s")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global rename_next_file
    if not context.args:
        await update.message.reply_text("❌ Usage: /rename <new_name>")
        return
    rename_next_file = context.args[0]
    await update.message.reply_text(f"✅ Next file will be renamed to: {rename_next_file}")

async def startqueue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not queue:
        await update.message.reply_text("📭 No files in queue. Use /add <link>")
        return
    await update.message.reply_text(f"⏳ Starting download queue ({len(queue)} files)...")
    asyncio.create_task(process_queue(update))

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_seconds = (datetime.utcnow()-start_time_bot).total_seconds()
    hours, remainder = divmod(int(uptime_seconds),3600)
    minutes, seconds = divmod(remainder,60)
    await update.message.reply_text(f"💓 Bot uptime: {hours}h {minutes}m {seconds}s")

async def reset_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global queue, is_downloading, rename_next_file, stats, current_task
    is_downloading = False
    queue.clear()
    stats = {"files":0, "size":0, "total_speed":0}
    rename_next_file = None
    current_task = None
    await update.message.reply_text(f"♻️ Bot v{BOT_VERSION} has been fully reset! Queue, stats, and current tasks cleared.")

# === Main ===
def main():
    Thread(target=run_flask).start()
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register commands
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("add", add))
    app_bot.add_handler(CommandHandler("list", list_queue))
    app_bot.add_handler(CommandHandler(["s","startqueue"], startqueue))
    app_bot.add_handler(CommandHandler("clear", clear_queue))
    app_bot.add_handler(CommandHandler("status", status))
    app_bot.add_handler(CommandHandler("cancel", cancel))
    app_bot.add_handler(CommandHandler("stats", stats_command))
    app_bot.add_handler(CommandHandler("rename", rename))
    app_bot.add_handler(CommandHandler("ping", ping))
    app_bot.add_handler(CommandHandler("reset", reset_bot))

    print(f"🤖 Bot v{BOT_VERSION} started successfully! Waiting for commands...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
