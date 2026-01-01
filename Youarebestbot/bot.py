import os
import random
import sqlite3
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from backboard import BackboardClient


# ===================== ENV =====================
TOKEN = os.getenv("TOKEN")
BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY")

BB_LLM_PROVIDER = os.getenv("BB_LLM_PROVIDER", "google")
BB_MODEL_NAME = os.getenv("BB_MODEL_NAME", "gemini-2.5-pro")

PUBLIC_URL = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")  # Render sometimes provides this
PORT = int(os.getenv("PORT", "10000"))

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tehran")

DB_NAME = os.getenv("DB_PATH", "bot.db")  # روی رایگان بهتره همین بمونه (Disk پولیه)


# ===================== Config =====================
DEFAULT_INTERVAL = 3600
SLEEP_START = 23
SLEEP_END = 8


# ===================== Messages =====================
MESSAGES = [
    "🌿 یادت نره: تو هم آدمی… حق داری خسته شی، حق داری آروم‌تر بری.",
    "💛 یه لحظه مکث کن… با خودت مهربون حرف بزن. همین الان.",
    "🫶 تو لازم نیست همیشه قوی باشی؛ همین که ادامه می‌دی یعنی قوی‌ای.",
    "✨ تو لازم نیست کامل باشی تا دوست‌داشتنی باشی.",
    "🌙 اگه امروز کم آوردی، به معنیِ بد بودنِ تو نیست؛ به معنیِ انسان بودنه.",
    "🌱 قدم‌های کوچیک هم پیشرفته؛ تو لازم نیست یک‌هو همه‌چی رو درست کنی.",
    "🧠 با خودت حرف بزن مثل کسی که دوستش داری… نه مثل کسی که سرزنشش می‌کنی.",
    "🫧 نفس عمیق… تو امنی. همه‌چیز قرار نیست همین الان حل بشه.",
    "🌟 تو ارزشمندتری از نتیجه‌هات. حتی وقتی هیچ کاری نکردی.",
    "🍃 با خودت مهربون باش؛ هیچ‌کس جز تو تا آخر همراهت نیست.",
    "🌤️ یه روز بد، یعنی یه روز بد… نه یه زندگی بد.",
    "💧 یه لیوان آب بخور؛ این هم یه جور «دوست داشتنِ خودته».",
    "🌸 خوددوستی یعنی: امروز هم با خودم بد حرف نمی‌زنم.",
    "🕊️ آروم برو… خودتو به مسابقه‌ای که نیست، نکشون.",
    "💬 به خودت بگو: «من دارم تلاش می‌کنم… همین قشنگه.»",
    "🌿 با خودت نرم باش… رشد با عشق میاد، نه با فشار.",
]


# ===================== Keyboards =====================
main_keyboard = ReplyKeyboardMarkup(
    [
        ["▶️ Start", "⏹ Stop"],
        ["📊 Status", "⏱ Interval"],
        ["💬 Chat On", "🚫 Chat Off"],
    ],
    resize_keyboard=True,
)

interval_keyboard = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("1 دقیقه", callback_data="int_1"),
         InlineKeyboardButton("5 دقیقه", callback_data="int_5")],
        [InlineKeyboardButton("30 دقیقه", callback_data="int_30"),
         InlineKeyboardButton("1 ساعت", callback_data="int_60")],
    ]
)


# ===================== DB =====================
def db_conn():
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")

    cur.execute("PRAGMA table_info(chats)")
    cols = {r[1] for r in cur.fetchall()}

    if "interval" not in cols:
        cur.execute("ALTER TABLE chats ADD COLUMN interval INTEGER DEFAULT 3600")
    if "is_active" not in cols:
        cur.execute("ALTER TABLE chats ADD COLUMN is_active INTEGER DEFAULT 0")
    if "chat_enabled" not in cols:
        cur.execute("ALTER TABLE chats ADD COLUMN chat_enabled INTEGER DEFAULT 1")
    if "bb_thread_id" not in cols:
        cur.execute("ALTER TABLE chats ADD COLUMN bb_thread_id TEXT")

    conn.commit()
    conn.close()

def ensure_chat(chat_id: int):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_chat_settings(chat_id: int):
    ensure_chat(chat_id)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT interval, is_active, chat_enabled, bb_thread_id FROM chats WHERE chat_id=?", (chat_id,))
    r = cur.fetchone()
    conn.close()
    return {"interval": int(r[0]), "is_active": int(r[1]), "chat_enabled": int(r[2]), "bb_thread_id": r[3]}

def set_interval_db(chat_id: int, interval: int):
    ensure_chat(chat_id)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE chats SET interval=? WHERE chat_id=?", (int(interval), chat_id))
    conn.commit()
    conn.close()

def set_active_db(chat_id: int, active: bool):
    ensure_chat(chat_id)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE chats SET is_active=? WHERE chat_id=?", (1 if active else 0, chat_id))
    conn.commit()
    conn.close()

def set_chat_enabled_db(chat_id: int, enabled: bool):
    ensure_chat(chat_id)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE chats SET chat_enabled=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
    conn.commit()
    conn.close()

def set_thread_id_db(chat_id: int, thread_id: str):
    ensure_chat(chat_id)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE chats SET bb_thread_id=? WHERE chat_id=?", (thread_id, chat_id))
    conn.commit()
    conn.close()

def get_active_chats():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT chat_id, interval FROM chats WHERE is_active=1")
    rows = cur.fetchall()
    conn.close()
    return [(int(a), int(b)) for a, b in rows]

def meta_get(key: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT v FROM meta WHERE k=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def meta_set(key: str, value: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO meta (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, value),
    )
    conn.commit()
    conn.close()


# ===================== Reminder =====================
def now_local():
    return datetime.now(ZoneInfo(TIMEZONE))

def is_sleep_time(start: int, end: int) -> bool:
    hour = now_local().hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end

def jobs_map(app: Application):
    return app.bot_data.setdefault("jobs", {})  # {chat_id: Job}

async def reminder(context: ContextTypes.DEFAULT_TYPE):
    if is_sleep_time(SLEEP_START, SLEEP_END):
        return
    await context.bot.send_message(chat_id=context.job.chat_id, text=random.choice(MESSAGES))

def restart_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, interval: int):
    jm = jobs_map(context.application)
    old = jm.get(chat_id)
    if old:
        old.schedule_removal()

    job = context.application.job_queue.run_repeating(
        reminder, interval=interval, first=interval, chat_id=chat_id, name=f"mot_{chat_id}"
    )
    jm[chat_id] = job

def stop_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    jm = jobs_map(context.application)
    job = jm.pop(chat_id, None)
    if job:
        job.schedule_removal()

def restore_jobs(app: Application):
    for chat_id, interval in get_active_chats():
        job = app.job_queue.run_repeating(
            reminder, interval=interval, first=interval, chat_id=chat_id, name=f"mot_{chat_id}"
        )
        jobs_map(app)[chat_id] = job


# ===================== Backboard Chat =====================
def get_bb_client(app: Application):
    if not BACKBOARD_API_KEY:
        return None
    client = app.bot_data.get("bb_client")
    if client:
        return client
    client = BackboardClient(api_key=BACKBOARD_API_KEY)
    app.bot_data["bb_client"] = client
    return client

def get_or_create_assistant_id_sync(app: Application):
    aid = meta_get("bb_assistant_id")
    if aid:
        return aid
    client = get_bb_client(app)
    if not client:
        return None
    assistant = client.create_assistant(name="Telegram Buddy", description="Persian friendly assistant.")
    aid = assistant.assistant_id
    meta_set("bb_assistant_id", aid)
    return aid

def get_or_create_thread_id_sync(app: Application, chat_id: int):
    s = get_chat_settings(chat_id)
    if s["bb_thread_id"]:
        return s["bb_thread_id"]
    client = get_bb_client(app)
    aid = get_or_create_assistant_id_sync(app)
    if not client or not aid:
        return None
    thread = client.create_thread(aid)
    tid = thread.thread_id
    set_thread_id_db(chat_id, tid)
    return tid

def backboard_reply_sync(app: Application, chat_id: int, text: str) -> str:
    client = get_bb_client(app)
    if not client:
        return "چت‌بات فعال نیست چون BACKBOARD_API_KEY ست نشده."
    tid = get_or_create_thread_id_sync(app, chat_id)
    if not tid:
        return "نتونستم گفتگو رو بسازم. کلید Backboard رو چک کن."
    try:
        resp = client.add_message(
            thread_id=tid,
            content=text,
            llm_provider=BB_LLM_PROVIDER,
            model_name=BB_MODEL_NAME,
            stream=False,
        )
        out = (resp.latest_message.content or "").strip()
        return (out or "یه لحظه نتونستم جواب بگیرم، دوباره بگو 🙃")[:3500]
    except Exception:
        return "الان نتونستم به سرویس چت وصل بشم. یکم بعد دوباره امتحان کن."

async def backboard_reply_async(app: Application, chat_id: int, text: str) -> str:
    return await asyncio.to_thread(backboard_reply_sync, app, chat_id, text)


def should_reply_in_group(update: Update, bot_username: str | None, bot_id: int) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False
    t = msg.text
    if bot_username and f"@{bot_username}" in t:
        return True
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == bot_id:
        return True
    return False


# ===================== Handlers =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = get_chat_settings(chat_id)
    if jobs_map(context.application).get(chat_id):
        await update.message.reply_text("⏳ فعاله 😉", reply_markup=main_keyboard)
        return
    restart_job(context, chat_id, s["interval"])
    set_active_db(chat_id, True)
    await update.message.reply_text(
        f"✅ فعال شد!\nهر {s['interval']//60} دقیقه یه تلنگرِ مهربون می‌فرستم 🫶",
        reply_markup=main_keyboard,
    )
    await context.bot.send_message(chat_id=chat_id, text=random.choice(MESSAGES))

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stop_job(context, chat_id)
    set_active_db(chat_id, False)
    await update.message.reply_text("⛔ متوقف شد.", reply_markup=main_keyboard)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = get_chat_settings(chat_id)
    active = "✅ فعاله" if s["is_active"] == 1 else "❌ خاموشه"
    chat_on = "✅ روشنه" if s["chat_enabled"] == 1 else "❌ خاموشه"
    await update.message.reply_text(
        f"📊 وضعیت\n{active}\n⏱ هر {s['interval']//60} دقیقه\n🌙 خواب: {SLEEP_START}:00 تا {SLEEP_END}:00\n💬 چت‌بات: {chat_on}",
        reply_markup=main_keyboard,
    )

async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏱ فاصله زمانی رو انتخاب کن:", reply_markup=interval_keyboard)

async def cmd_chaton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    set_chat_enabled_db(chat_id, True)
    await update.message.reply_text("💬 چت‌بات روشن شد ✅", reply_markup=main_keyboard)

async def cmd_chatoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    set_chat_enabled_db(chat_id, False)
    await update.message.reply_text("🚫 چت‌بات خاموش شد ✅", reply_markup=main_keyboard)

async def handle_interval_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id
    minutes = int(q.data.split("_")[1])
    interval = minutes * 60
    set_interval_db(chat_id, interval)
    if get_chat_settings(chat_id)["is_active"] == 1:
        restart_job(context, chat_id, interval)
    await context.bot.send_message(chat_id=chat_id, text=random.choice(MESSAGES))
    await q.message.reply_text(f"✅ تنظیم شد!\nهر {minutes} دقیقه یه تلنگرِ مهربون می‌فرستم 🫶", reply_markup=main_keyboard)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    text = (update.message.text or "").strip()

    # Keyboard buttons
    if text == "▶️ Start":
        await cmd_start(update, context); return
    if text == "⏹ Stop":
        await cmd_stop(update, context); return
    if text == "📊 Status":
        await cmd_status(update, context); return
    if text == "⏱ Interval":
        await cmd_interval(update, context); return
    if text == "💬 Chat On":
        await cmd_chaton(update, context); return
    if text == "🚫 Chat Off":
        await cmd_chatoff(update, context); return

    s = get_chat_settings(chat_id)
    if s["chat_enabled"] != 1:
        return

    # Group rule: only mention/reply
    if chat_type != "private":
        if not should_reply_in_group(update, context.bot.username, context.bot.id):
            return
        if context.bot.username:
            text = text.replace(f"@{context.bot.username}", "").strip()
            if not text:
                return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await backboard_reply_async(context.application, chat_id, text)
    await update.message.reply_text(reply)


# ===================== Webhook Server =====================
async def telegram_webhook(request: Request) -> Response:
    data = await request.json()
    await request.app.state.ptb_app.update_queue.put(Update.de_json(data=data, bot=request.app.state.ptb_app.bot))
    return Response()

async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")

async def ping(_: Request) -> PlainTextResponse:
    return PlainTextResponse("pong")


async def main():
    if not TOKEN:
        raise RuntimeError("TOKEN env var is not set")
    if not PUBLIC_URL:
        raise RuntimeError("PUBLIC_URL env var is not set (example: https://your-app.onrender.com)")

    init_db()

    ptb_app = ApplicationBuilder().token(TOKEN).updater(None).build()

    ptb_app.add_handler(CommandHandler("start", cmd_start))
    ptb_app.add_handler(CommandHandler("stop", cmd_stop))
    ptb_app.add_handler(CommandHandler("status", cmd_status))
    ptb_app.add_handler(CommandHandler("interval", cmd_interval))
    ptb_app.add_handler(CommandHandler("chaton", cmd_chaton))
    ptb_app.add_handler(CommandHandler("chatoff", cmd_chatoff))

    ptb_app.add_handler(CallbackQueryHandler(handle_interval_buttons, pattern=r"^int_\d+$"))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # restore reminders from DB
    restore_jobs(ptb_app)

    # set telegram webhook (HTTPS is required) :contentReference[oaicite:6]{index=6}
    await ptb_app.bot.set_webhook(url=f"{PUBLIC_URL}/telegram", allowed_updates=Update.ALL_TYPES)

    # starlette server
    starlette_app = Starlette(
        routes=[
            Route("/telegram", telegram_webhook, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
            Route("/ping", ping, methods=["GET"]),
        ]
    )
    starlette_app.state.ptb_app = ptb_app

    server = uvicorn.Server(
        uvicorn.Config(app=starlette_app, host="0.0.0.0", port=PORT, log_level="info")
    )

    async with ptb_app:
        await ptb_app.start()
        await server.serve()
        await ptb_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
