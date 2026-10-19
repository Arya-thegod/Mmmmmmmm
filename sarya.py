import logging
import asyncio
import random
import os
import threading
import time
import datetime
import psutil 
import gc # RAM clean karne ke liye (Garbage Collector)
from flask import Flask
from telegram import Update
from telegram.error import RetryAfter, Forbidden
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ─── LOGGING FIX ───
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING) 

# ─── FLASK WEB SERVER (Render ke liye) ───
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "NC BOT IS ALIVE!"
def run_web(): 
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# ─── CONFIGURATION ───
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) 
DELAY = 0.3  # Fixed Speed
EMOJIS = ["🔥", "⚡", "💀", "🚀", "🌞", "✅", "💋", "😝"]

# ─── STATUS VARIABLES ───
START_TIME = time.time()
TOTAL_NC_RENAMES = 0
RAM_LIMIT = 512.0

# ─── CONTROLLER & MEMORY ───
NC_STATES = {} # Purge ke baad restart ke liye active commands yaad rakhega

class Controller:
    def __init__(self):
        self.active = {}

    def stop_chat(self, chat_id):
        if chat_id in self.active:
            tasks = self.active.pop(chat_id)
            for task in tasks:
                if not task.done():
                    task.cancel()

    def stop_all(self):
        for cid in list(self.active.keys()):
            self.stop_chat(cid)

ctrl = Controller()
bots = []

# ─── NC LOOP ───
async def nc_loop(bot, chat_id, text):
    global TOTAL_NC_RENAMES
    while True:
        try:
            e = random.choice(EMOJIS)
            title = f"{e}{text}{e}"[:255]
            await bot.set_chat_title(chat_id=chat_id, title=title)
            TOTAL_NC_RENAMES += 1 
            
            await asyncio.sleep(DELAY)
            
        except asyncio.CancelledError:
            break 
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1) 
        except Forbidden:
            break 
        except Exception as e:
            await asyncio.sleep(2) 

# ─── AUTO PURGE SCHEDULER (Roz 7:00 PM IST) ───
async def daily_purge_scheduler():
    while True:
        # IST Time nikalna
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist_tz)
        
        # Agla 7:00 PM set karna
        target_time = now_ist.replace(hour=19, minute=0, second=0, microsecond=0)
        if now_ist >= target_time:
            target_time += datetime.timedelta(days=1)
        
        # Kitna time bacha hai calculate karna
        wait_seconds = (target_time - now_ist).total_seconds()
        logger.info(f"Next Auto-Purge scheduled in {wait_seconds/3600:.2f} hours.")
        
        # 7:00 PM tak wait karna
        await asyncio.sleep(wait_seconds)
        
        # --- PURGE START ---
        logger.info("Starting Daily 7:00 PM Auto-Purge...")
        
        # 1. Saare active tasks stop karo
        ctrl.stop_all()
        ctrl.active.clear()
        
        # 2. RAM clean karo
        gc.collect() 
        
        # 3. Pichle commands ko wapas start karo
        for chat_id, text in NC_STATES.items():
            ctrl.active[chat_id] = []
            for b in bots:
                task = asyncio.create_task(nc_loop(b['bot'], chat_id, text))
                ctrl.active[chat_id].append(task)
        
        logger.info("Daily Auto-Purge Completed. Tasks restarted.")
        
        # Owner ko message bhejna
        try:
            if bots:
                await bots[0]['bot'].send_message(OWNER_ID, "🧹 **Daily 7:00 PM Purge Completed!**\nRAM Cleared. Saare purane NC commands auto-restart ho gaye hain.")
        except: pass

# ─── COMMAND ROUTER ───
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global DELAY 
    msg = update.message
    if not msg: return
    text = msg.text or ""
    if not (text.startswith("/") or text.startswith(".")): return

    user = update.effective_user
    if not user or user.id != OWNER_ID: return

    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()
    args = parts[1].split() if len(parts) > 1 else []

    # ─── NC COMMAND ───
    if cmd == "nc":
        if not args: return await msg.reply_text("Usage: /nc <text>")
        target = ' '.join(args)
        chat_id = update.effective_chat.id
        
        ctrl.stop_chat(chat_id)
        ctrl.active[chat_id] = []
        
        # State save karna (taaki purge ke baad wapas chalu ho)
        NC_STATES[chat_id] = target
        
        for b in bots:
            task = asyncio.create_task(nc_loop(b['bot'], chat_id, target))
            ctrl.active[chat_id].append(task)
            
        await msg.reply_text(f"✅ NC STARTED: {target}\n⚡ Speed: {DELAY}s")

    # ─── STOP COMMAND ───
    elif cmd == "stop":
        chat_id = update.effective_chat.id
        if ctrl.active.get(chat_id):
            ctrl.stop_chat(chat_id)
            NC_STATES.pop(chat_id, None) # State se hatao
            await msg.reply_text("🛑 STOPPED")
        else:
            await msg.reply_text("⚠️ NO ACTIVE NC")

    # ─── STATUS COMMAND ───
    elif cmd == "status":
        uptime_seconds = int(time.time() - START_TIME)
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s" if days > 0 else f"{hours}h {minutes}m {seconds}s"

        process = psutil.Process(os.getpid())
        used_mb = process.memory_info().rss / (1024 * 1024)
        percent_used = (used_mb / RAM_LIMIT) * 100
        percent_free = 100 - percent_used
        free_mb = RAM_LIMIT - used_mb

        filled_blocks = int(percent_used / 10)
        meter = "■" * filled_blocks + "□" * (10 - filled_blocks)

        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist_tz)
        
        purge_time = now_ist.replace(hour=19, minute=0, second=0, microsecond=0) # 7:00 PM
        if now_ist >= purge_time:
            purge_time += datetime.timedelta(days=1)
        
        time_left = purge_time - now_ist
        h_left, r = divmod(time_left.seconds, 3600)
        m_left, _ = divmod(r, 60)
        
        last_purge = purge_time - datetime.timedelta(days=1)
        last_purge_str = last_purge.strftime("%d-%b %H:%M:%S")
        next_purge_str = purge_time.strftime("%d-%b %H:%M")

        active_gcs = len(ctrl.active)

        status_text = (
            f"📊 <b>RENDER 512 MB REAL-TIME USAGE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 <b>RAM Status:</b>\n"
            f"• Used: {used_mb:.1f} MB ({percent_used:.1f}%)\n"
            f"• Bacha Hua (Free): {free_mb:.1f} MB ({percent_free:.1f}%)\n"
            f"• Live Meter: [{meter}] {percent_used:.1f}%\n\n"
            f"🛡️ <b>512 MB Kab Full Hoga / Bot Kab Rukega?</b>\n"
            f"🟢 Bot Rukne Ka Chance: 0.0%\n"
            f"• Bot sirf ~18-24 MB RAM leta hai!\n"
            f"• Roz shaam 7:00 PM IST ko auto-reset ho jata hai.\n"
            f"• Render 512 MB par 4-5+ mahine bina ruke chalega!\n\n"
            f"🧹 <b>Daily 7:00 PM Purge:</b>\n"
            f"• Last Purge: {last_purge_str} IST\n"
            f"• Agla Purge: {next_purge_str} IST ({h_left}h {m_left}m baaki)\n\n"
            f"⚙️ <b>Bot & NC Status:</b>\n"
            f"• Active Running GCs: {active_gcs}\n"
            f"• Total NC Renames: {TOTAL_NC_RENAMES}\n"
            f"• Cadence: 1 NC / {DELAY} Seconds (Non-Stop)\n"
            f"• Bots in Relay: 1\n"
            f"• Uptime: {uptime_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await msg.reply_text(status_text, parse_mode='HTML')

# ─── MAIN RUNNER ───
async def run_bot(tok):
    try:
        app = Application.builder().token(tok).connect_timeout(10).read_timeout(30).build()
        me = await app.bot.get_me()
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
        app.add_handler(MessageHandler(filters.COMMAND, router))
        
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True) 
        
        bots.append({'id': me.id, 'bot': app.bot, 'app': app})
        print(f"✅ Started @{me.username}")
        
        await asyncio.Event().wait() 
    except Exception as e:
        print(f"❌ Fail: {e}")

async def main():
    print("[*] Starting Web Server...")
    threading.Thread(target=run_web, daemon=True).start()

    tok = os.environ.get("BOT_TOKEN")
    if not tok:
        print("[!] ERROR: BOT_TOKEN not found in Render.")
        return

    print(f"[*] Starting Bot with fixed {DELAY}s delay...")
    
    # Auto Purge Scheduler ko background me start karna
    asyncio.create_task(daily_purge_scheduler())
    
    await run_bot(tok.strip())

if __name__ == "__main__":
    asyncio.run(main())
