import os
import asyncio
import requests
import yt_dlp
import time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread
from datetime import datetime

# === Load BOT TOKEN from Environment ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN not found in environment variables!")
else:
    print("✅ BOT_TOKEN loaded successfully!")

# === Flask server (for uptime ping) ===
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Bot is alive and running!"
def run_flask():
    app.run(host="0.0.0.0", port=8080)

# === Global variables ===
queue = []
is_downloading = False
rename_next_file = None
stats = {"files":0, "size":0, "total_speed":0}
current_task = None
start_time_bot = datetime.utcnow()

# === Helper: Progress message ===
async def update_progress_message(message, prefix, downloaded, total, start_time):
    elapsed = time.time() - start_time
    speed = downloaded / (1024*1024*elapsed + 0.0001)
    percent = (downloaded/total)*100 if total>0 else 0
    remaining = total - downloaded
    eta = remaining / (1024*1024*speed + 0.0001)
    text = f"{prefix} {percent:.2f}%\n⚡ Speed: {speed:.2f} MB/s\n⏳ ETA: {eta:.1f}s"
    await message.edit_text(text)

# === Download function ===
async def download_file(update: Update, url: str):
    global is_downloading, rename_next_file, stats, current_task
    current_task = url
    is_downloading = True
    filename = None
    pinned_msg = await update.message.reply_text(f"🚀 Download started: {url}")

    try:
        # --- YouTube or streaming ---
        if "youtube.com" in url or "youtu.be" in url:
            ydl_opts = {'format': 'best', 'outtmpl': '%(title)s.%(ext)s'}
            if rename_next_file:
                ydl_opts['outtmpl'] = rename_next_file + ".%(ext)s"

            loop = asyncio.get_event_loop()
            def run_yt_dlp():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            await loop.run_in_executor(None, run_yt_dlp)

            filename = ydl_opts['outtmpl'].replace("%(ext)s","mp4") if rename_next_file else "video.mp4"

        # --- Direct download link ---
        else:
            filename = url.split("/")[-1]
            if rename_next_file:
                ext = filename.split(".")[-1] if "." in filename else ""
                filename = f"{rename_next_file}.{ext}" if ext else rename_next_file
                rename_next_file = None

            headers = {"User-Agent": "Mozilla/5.0"}
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    with requests.get(url, headers=headers, stream=True, timeout=(10, None)) as r:
                        r.raise_for_status()
                        total_length = int(r.headers.get('content-length',0))
                        downloaded = 0
                        chunk_size = 1024*1024
                        start_time = time.time()
                        with open(filename,'wb') as f:
                            for chunk in r.iter_content(chunk_size=chunk_size):
                                if not is_downloading:
                                    raise Exception("Download canceled")
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if time.time()-start_time>1:
                                        await update_progress_message(pinned_msg,"⬇️ Downloading",downloaded,total_length,start_time)
                                        start_time = time.time()
                    break
                except requests.exceptions.RequestException as e:
                    if attempt < max_retries-1:
                        await pinned_msg.edit_text(f"⚠️ Retry {attempt+1}/{max_retries} after error: {e}")
                        await asyncio.sleep(2)
                    else:
                        raise e

        if not is_downloading:
            if filename and os.path.exists(filename):
                os.remove(filename)
            await pinned_msg.edit_text("⚠️ Download canceled mid-way.")
            return

        # --- Update stats ---
        if os.path.exists(filename):
            file_size = os.path.getsize(filename)
            stats["files"] += 1
            stats["size"] += file_size
            stats["total_speed"] += (file_size/1024/1024)/max(time.time()-start_time,0.1)

        # --- Upload file safely ---
        with open(filename,'rb') as f:
            await update.message.reply_document(document=f, filename=filename)
        os.remove(filename)
        await pinned_msg.edit_text(f"🎉 Upload complete: {filename}")

    except Exception as e:
        await pinned_msg.edit_text(f"❌ Error: {e}")
        if filename and os.path.exists(filename):
            os.remove(filename)
    finally:
        is_downloading = False

# === Queue Processor (non-blocking) ===
async def process_queue(update: Update):
    global is_downloading
    while queue:
        if not is_downloading:
            next_url = queue.pop(0)
            await download_file(update, next_url)
        await asyncio.sleep(1)

# === Bot Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is alive! Use /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Downloader Bot — Commands*\n\n"
        "/add <link> — Add link to queue\n"
        "/s or /startqueue — Start download queue\n"
        "/list — Show queued links\n"
        "/clear — Clear queue\n"
        "/status — Show current task\n"
        "/cancel — Cancel current download\n"
        "/stats — Show session stats\n"
        "/rename <new_name> — Rename next file\n"
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
        text = "\n".join([f"{i+1}. {url}" for i,url in enumerate(queue)])
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

# === Main ===
def main():
    Thread(target=run_flask).start()  # Start Flask server
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

    print("🤖 Bot started successfully! Waiting for commands...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
