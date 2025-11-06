iimport os
import asyncio
import requests
import time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ✅ Correct way
BOT_TOKEN = os.getenv("BOT_TOKEN")

print("DEBUG BOT_TOKEN:", BOT_TOKEN)

# --- Flask for Render Keep Alive ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is alive on Render!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# --- Globals ---
download_queue = []
is_downloading = False
current_task = None

# --- Helper Function for Progress ---
async def update_progress(update, prefix, downloaded, total, start_time):
    elapsed = time.time() - start_time
    speed = downloaded / (1024 * 1024 * elapsed + 0.0001)  # MB/s
    percent = (downloaded / total) * 100
    remaining = total - downloaded
    eta = remaining / (1024 * 1024 * speed + 0.0001)
    text = (f"{prefix} {percent:.2f}%\n"
            f"⚡ Speed: {speed:.2f} MB/s\n"
            f"⏳ ETA: {eta:.1f}s")
    await update.message.reply_text(text)

# --- Commands ---

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Welcome to the Smart Video Downloader Bot!*\n\n"
        "Here’s what I can do for you 👇\n\n"
        "🎬 /add `<link>` - Add a video link to the download queue\n"
        "📦 /list - Show all queued videos\n"
        "🚀 /s or /startqueue - Start downloading all queued videos\n"
        "🗑️ /clear - Clear all queued links\n"
        "⏳ /status - Show current task status\n"
        "🛑 /cancel - Cancel the current download\n"
        "📘 /help - Show this help message\n\n"
        "⚡ *Bonus Features:*\n"
        "- Live Download Progress (%)\n"
        "- Speed (MB/s) + Estimated Time Remaining\n"
        "- Auto Next Task After Completion\n"
        "- Flask Keep-Alive (24×7 uptime on Render)\n\n"
        "💡 *Tip:* You can queue multiple links using /add multiple times."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a video link.\nExample: `/add https://example.com/video.mp4`", parse_mode="Markdown")
        return
    link = context.args[0]
    download_queue.append(link)
    await update.message.reply_text(f"✅ Added to queue: {link}\n📦 Total queued: {len(download_queue)}")

async def list_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not download_queue:
        await update.message.reply_text("📭 Queue is empty.")
    else:
        text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(download_queue)])
        await update.message.reply_text(f"📦 *Download Queue:*\n{text}", parse_mode="Markdown")

async def clear_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    download_queue.clear()
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

# --- Download with Progress ---
async def download_file(update, url):
    global is_downloading

    filename = "video.mp4"
    start_time = time.time()

    try:
        with requests.get(url, stream=True, timeout=120) as r:
            total_length = int(r.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB

            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not is_downloading:
                        break
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if time.time() - start_time > 2:  # update every ~2 sec
                            await update_progress(update, "⬇️ Downloading", downloaded, total_length, start_time)
                            start_time = time.time()

        if not is_downloading:
            os.remove(filename)
            await update.message.reply_text("⚠️ Download canceled mid-way.")
            return None

        await update.message.reply_text("✅ Download complete, starting upload...")

        # Upload progress simulation
        file_size = os.path.getsize(filename)
        uploaded = 0
        chunk = file_size / 10  # fake chunks for updates
        for _ in range(10):
            await asyncio.sleep(1)
            uploaded += chunk
            percent = (uploaded / file_size) * 100
            await update.message.reply_text(f"📤 Uploading... {percent:.0f}%")

        # Final upload to Telegram
        await update.message.reply_video(video=open(filename, 'rb'))
        os.remove(filename)
        await update.message.reply_text("✅ Upload completed!")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        if os.path.exists(filename):
            os.remove(filename)
        return None

# --- Queue Handler ---
async def start_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_downloading, current_task

    if is_downloading:
        await update.message.reply_text("⚙️ Already working on a task.")
        return

    if not download_queue:
        await update.message.reply_text("📭 No videos queued. Use /add <link>")
        return

    is_downloading = True
    await update.message.reply_text(f"🚀 Starting download queue ({len(download_queue)} files)...")

    while download_queue and is_downloading:
        current_task = download_queue.pop(0)
        await update.message.reply_text(f"🎬 Now downloading:\n{current_task}")
        await download_file(update, current_task)

    is_downloading = False
    await update.message.reply_text("🏁 All tasks done or canceled!")

# --- Main Function ---
def main():
    Thread(target=run_flask).start()
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("add", add_link))
    app_bot.add_handler(CommandHandler("list", list_queue))
    app_bot.add_handler(CommandHandler(["s", "startqueue"], start_queue))
    app_bot.add_handler(CommandHandler("clear", clear_queue))
    app_bot.add_handler(CommandHandler("status", status))
    app_bot.add_handler(CommandHandler("cancel", cancel))

    print("🤖 Bot is running with live speed + ETA tracking...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
