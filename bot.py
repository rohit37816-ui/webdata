import os
import asyncio
import requests
import time
from urllib.parse import urlparse, parse_qs, unquote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, CallbackQueryHandler, filters
from flask import Flask
from threading import Thread
from datetime import datetime
import re

# === Bot Version ===
BOT_VERSION = "v0.1"

# === Load BOT TOKEN ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN not found!")
else:
    print(f"✅ BOT_TOKEN loaded! Bot version: {BOT_VERSION}")

# === Flask server for uptime ===
app = Flask(__name__)
@app.route('/')
def home():
    return f"✅ Bot {BOT_VERSION} is alive!"
def run_flask():
    app.run(host="0.0.0.0", port=8080)

# === Globals ===
is_downloading = False
current_task = None
start_time_bot = datetime.utcnow()
SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
URL_REGEX = re.compile(r'https?://[^\s]+')
queue = []

# === Helpers ===
def parse_direct_link(url):
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        real_url = unquote(qs.get("play", [url])[0])
        title = qs.get("title", ["video"])[0].replace("+"," ").strip()
        if not title:
            title = "video"
        return real_url, title
    except Exception:
        return url, "video"

async def progress_animation(message, prefix, downloaded, total, start_time, spinner_index=0):
    percent = (downloaded/total)*100 if total>0 else 0
    elapsed = time.time() - start_time
    speed = downloaded / (1024*1024*elapsed + 0.0001)
    remaining = total - downloaded
    eta = remaining / (1024*1024*speed + 0.0001)
    text = f"{prefix} {SPINNER[spinner_index%len(SPINNER)]}\n" \
           f"📁 {downloaded/1024/1024:.2f}/{total/1024/1024:.2f} MB\n" \
           f"⚡ Speed: {speed:.2f} MB/s | ⏳ ETA: {eta:.1f}s"
    try:
        await message.edit_text(text)
    except:
        pass

# === Download function ===
async def download_video(update: Update, url: str, filename: str):
    global is_downloading, current_task
    current_task = url
    is_downloading = True
    pinned_msg = await update.message.reply_text(f"🚀 Download started: {filename}")
    spinner_index = 0
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with requests.get(url, headers=headers, stream=True, timeout=(10,None)) as r:
            r.raise_for_status()
            total_length = int(r.headers.get('content-length',0))
            downloaded = 0
            chunk_size = 1024*1024
            start_time = time.time()
            with open(filename,'wb') as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if time.time()-start_time > 1:
                            await progress_animation(pinned_msg,"⬇️ Downloading",downloaded,total_length,start_time, spinner_index)
                            spinner_index += 1
                            start_time = time.time()
        await pinned_msg.edit_text(f"⬆️ Download finished. Uploading: {filename} ...")
        with open(filename,'rb') as f:
            await update.message.reply_document(document=InputFile(f, filename=filename))
        os.remove(filename)
        await pinned_msg.edit_text(f"🎉 Upload complete: {filename}")
    except Exception as e:
        await pinned_msg.edit_text(f"❌ Error: {e}")
        if os.path.exists(filename):
            os.remove(filename)
    finally:
        is_downloading = False
        current_task = None

# === Queue Processor ===
async def process_queue(update: Update):
    while queue:
        if not is_downloading:
            url = queue.pop(0)
            real_url, title = parse_direct_link(url)
            filename = f"{title}.mp4"
            await download_video(update, real_url, filename)
        await asyncio.sleep(1)

# === Handlers ===
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or update.message.caption
    if not text:
        return
    urls = URL_REGEX.findall(text)
    if not urls:
        return
    for url in urls:
        real_url, title = parse_direct_link(url)
        queue.append(url)
        keyboard = [[InlineKeyboardButton("Download MP4", callback_data=url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Select the desired format👇\n"
            f"ғɪʟᴇɴᴀᴍᴇ: {title}\n"
            f"Tap to copy ☝️ Filename",
            reply_markup=reply_markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = query.data
    real_url, title = parse_direct_link(url)
    filename = f"{title}.mp4"
    await download_video(update, real_url, filename)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"✅ Bot {BOT_VERSION} is alive! Send a video link to start downloading.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"🤖 *Downloader Bot {BOT_VERSION} — Commands*\n\n"
        "/start — Start bot\n"
        "/help — Show this help\n"
        "/ping — Show uptime\n\n"
        "Send any direct link to get interactive download buttons.\n"
        "Bot automatically fetches video title and downloads original MP4."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_seconds = (datetime.utcnow()-start_time_bot).total_seconds()
    hours, remainder = divmod(int(uptime_seconds),3600)
    minutes, seconds = divmod(remainder,60)
    await update.message.reply_text(f"💓 Bot uptime: {hours}h {minutes}m {seconds}s")

# === Main ===
def main():
    Thread(target=run_flask).start()
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("ping", ping))

    # Catch any message containing a URL
    app_bot.add_handler(MessageHandler(filters.Regex(URL_REGEX), handle_link))
    # Button callback
    app_bot.add_handler(CallbackQueryHandler(button_handler))

    print(f"🤖 Bot {BOT_VERSION} started successfully!")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
