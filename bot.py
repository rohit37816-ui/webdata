import os
import asyncio
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Flask server (for Render keep-alive) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# --- Bot Core ---
download_queue = []
is_downloading = False
current_task = None

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📜 *Available Commands:*\n"
        "/add <link> - Add new video link to queue\n"
        "/list - Show current download queue\n"
        "/s - Start downloading queued videos\n"
        "/clear - Clear all queued tasks\n"
        "/status - Show current status\n"
        "/cancel - Cancel current download\n"
        "/help - Show this help message"
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
        await update.message.reply_text(f"📦 *Current Queue:*\n{text}", parse_mode="Markdown")

async def clear_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    download_queue.clear()
    await update.message.reply_text("🗑️ All queued tasks cleared!")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_downloading:
        await update.message.reply_text(f"⏳ Currently downloading:\n{current_task}")
    else:
        await update.message.reply_text("✅ Bot is idle. No active downloads.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_downloading, current_task
    if is_downloading:
        is_downloading = False
        await update.message.reply_text("🛑 Current download canceled!")
    else:
        await update.message.reply_text("⚠️ No active download to cancel.")

async def start_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_downloading, current_task

    if is_downloading:
        await update.message.reply_text("⚙️ Already downloading! Wait for current task to finish.")
        return

    if not download_queue:
        await update.message.reply_text("📭 Queue is empty. Add some links with /add.")
        return

    is_downloading = True
    await update.message.reply_text(f"🚀 Starting download queue ({len(download_queue)} files)...")

    while download_queue and is_downloading:
        current_task = download_queue.pop(0)
        await update.message.reply_text(f"⬇️ Downloading: {current_task}")

        try:
            filename = "video.mp4"
            r = requests.get(current_task, stream=True, timeout=120)
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not is_downloading:
                        break
                    if chunk:
                        f.write(chunk)

            if is_downloading:
                await update.message.reply_video(video=open(filename, 'rb'))
                os.remove(filename)
                await update.message.reply_text("✅ Upload complete, moving to next task...")

        except Exception as e:
            await update.message.reply_text(f"❌ Error downloading: {e}")

    is_downloading = False
    await update.message.reply_text("🏁 All tasks completed or canceled!")

# --- Main ---
def main():
    Thread(target=run_flask).start()  # Run Flask in background
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("add", add_link))
    app_bot.add_handler(CommandHandler("list", list_queue))
    app_bot.add_handler(CommandHandler(["s", "startqueue"], start_queue))
    app_bot.add_handler(CommandHandler("clear", clear_queue))
    app_bot.add_handler(CommandHandler("status", status))
    app_bot.add_handler(CommandHandler("cancel", cancel))

    print("🤖 Bot is running...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
