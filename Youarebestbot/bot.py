import os
import random
import sqlite3
import asyncio
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ChatAction
from telegram.ext import (
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

# برای Render دیسک: DB_PATH=/var/data/bot.db
DB_NAME = os.getenv("DB_PATH", "bot.db")

BB_LLM_PROVIDER = os.getenv("BB_LLM_PROVIDER", "google")
BB_MODEL_NAME = os.getenv("BB_MODEL_NAME", "gemini-2.5-pro")


# ===================== Config =====================
DEFAULT_INTERVAL = 3600  # 1 hour
SLEEP_START = 23
SLEEP_END = 8


# ===================== Messages (Self-love) =====================
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
        [
            InlineKeyboardButton("1 دقیقه", callback_data="int_1"),
            InlineKeyboardButton("5 دقیقه", callback_data="int_5"),
        ],
        [
            InlineKeyboardButton("30 دقیقه", callback_data="int_30"),
            InlineKeyboardButton("1 ساعت", callback_data="int_60"),
        ],
    ]
)


# ===================== DB =====================
def db_conn():
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            k TEXT PRIMARY KEY,
            v TEXT
        )
    """)

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
    row = cur.fetchone()
    conn.close()
    return {
        "interval": int(row[0]),
        "is_active": int(row[1]),
        "chat_enabled": int(row[2]),
        "bb_thread_id": row[3],
    }

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
    return [(int(r[0]), int(r[1])) for r in rows]

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
        "INSERT INTO meta (k, v) VALUES (?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, value),
    )
    conn.commit()
    conn.close()


# ===================== Reminder =====================
def is_sleep_time(start: int, end: int) -> bool:
    hour = datetime.now().hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end

def jobs_map(app):
    return app.bot_data.setdefault("jobs", {})  # {chat_id: Job}

async def reminder(context: ContextTypes.DEFAULT_TYPE):
    if is_sleep_time(SLEEP_START, SLEEP_END):
        return
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=random.choice(MESSAGES),
    )

def restart_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, interval: int):
    jm = jobs_map(context.application)
    old = jm.get(chat_id)
    if old:
        old.schedule_removal()

    job = context.application.job_queue.run_repeating(
        reminder,
        interval=interval,
        first=interval,
        chat_id=chat_id,
        name=f"mot_{chat_id}",
    )
    jm[chat_id] = job

def stop_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    jm = jobs_map(context.application)
    job = jm.pop(chat_id, None)
    if job:
        job.schedule_removal()


# ===================== Backboard Chat (sync SDK -> run in thread) =====================
def get_bb_client(app) -> BackboardClient | None:
    if not BACKBOARD_API_KEY:
        return None
    client = app.bot_data.get("bb_client")
    if client:
        return client
    client = BackboardClient(api_key=BACKBOARD_API_KEY, timeout=30, max_retries=2)
    app.bot_data["bb_client"] = client
    return client

def get_or_create_assistant_id_sync(app) -> str | None:
    assistant_id = meta_get("bb_assistant_id")
    if assistant_id:
        return assistant_id

    client = get_bb_client(app)
    if not client:
        return None

    assistant = client.create_assistant(
        name="Telegram Buddy",
        description="A friendly Persian assistant for chat + daily reminders.",
    )
    assistant_id = assistant.assistant_id
    meta_set("bb_assistant_id", assistant_id)
    return assistant_id

def get_or_create_thread_id_sync(app, chat_id: int) -> str | None:
    s = get_chat_settings(chat_id)
    if s["bb_thread_id"]:
        return s["bb_thread_id"]

    client = get_bb_client(app)
    assistant_id = get_or_create_assistant_id_sync(app)
    if not client or not assistant_id:
        return None

    thread = client.create_thread(assistant_id)
    thread_id = thread.thread_id
    set_thread_id_db(chat_id, thread_id)
    return thread_id

def backboard_reply_sync(app, chat_id: int, user_text: str) -> str:
    client = get_bb_client(app)
    if not client:
        return "چت‌بات فعلاً فعال نیست چون BACKBOARD_API_KEY ست نشده."

    thread_id = get_or_create_thread_id_sync(app, chat_id)
    if not thread_id:
        return "نتونستم گفتگو رو بسازم. (کلید Backboard رو چک کن)"

    try:
        resp = client.add_message(
            thread_id=thread_id,
            content=user_text,
            llm_provider=BB_LLM_PROVIDER,
            model_name=BB_MODEL_NAME,
            stream=False,
        )
        out = (resp.latest_message.content or "").strip()
        return (out or "یه لحظه نتونستم جواب درست بگیرم. دوباره بگو 🙃")[:3500]
    except Exception:
        return "الان نتونستم به سرویس چت وصل بشم. یه کم بعد دوباره امتحان کن."

async def backboard_reply_async(app, chat_id: int, user_text: str) -> str:
    # SDK sync هست -> توی thread اجرا می‌کنیم تا event loop قفل نشه
    return await asyncio.to_thread(backboard_reply_sync, app, chat_id, user_text)


def should_reply_in_group(update: Update, bot_username: str | None, bot_id: int) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False

    text = msg.text

    # اگر منشن شده
    if bot_username and f"@{bot_username}" in text:
        return True

    # اگر ریپلای به پیام ربات بوده
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == bot_id:
        return True

    return False


# ===================== Commands =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = get_chat_settings(chat_id)
    interval = s["interval"]

    if jobs_map(context.application).get(chat_id):
        await update.message.reply_text("⏳ فعاله 😉", reply_markup=main_keyboard)
        return

    restart_job(context, chat_id, interval)
    set_active_db(chat_id, True)

    await update.message.reply_text(
        f"✅ فعال شد!\nهر {interval // 60} دقیقه یه تلنگرِ مهربون می‌فرستم 🫶",
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


# ===================== Callback =====================
async def handle_interval_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    minutes = int(query.data.split("_")[1])
    interval = minutes * 60

    set_interval_db(chat_id, interval)

    s = get_chat_settings(chat_id)
    if s["is_active"] == 1:
        restart_job(context, chat_id, interval)

    await context.bot.send_message(chat_id=chat_id, text=random.choice(MESSAGES))
    await query.message.reply_text(
        f"✅ تنظیم شد!\nهر {minutes} دقیقه یه تلنگرِ مهربون می‌فرستم 🫶",
        reply_markup=main_keyboard,
    )


# ===================== Text Handler =====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    text = (update.message.text or "").strip()

    # ReplyKeyboard buttons
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

    # Chatbot enabled?
    s = get_chat_settings(chat_id)
    if s["chat_enabled"] != 1:
        return

    # In groups: only reply when mentioned or replied-to
    if chat_type != "private":
        bot_username = context.bot.username
        bot_id = context.bot.id
        if not should_reply_in_group(update, bot_username, bot_id):
            return

        # remove mention from text
        if bot_username:
            text = text.replace(f"@{bot_username}", "").strip()
            if not text:
                return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await backboard_reply_async(context.application, chat_id, text)
    await update.message.reply_text(reply)


# ===================== Restore Jobs =====================
def restore_jobs(app):
    for chat_id, interval in get_active_chats():
        jm = jobs_map(app)
        old = jm.get(chat_id)
        if old:
            old.schedule_removal()

        job = app.job_queue.run_repeating(
            reminder,
            interval=interval,
            first=interval,
            chat_id=chat_id,
            name=f"mot_{chat_id}",
        )
        jm[chat_id] = job


# ===================== Main =====================
def main():
    if not TOKEN:
        raise RuntimeError("TOKEN env var is not set")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()
    app.bot_data["jobs"] = {}

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("chaton", cmd_chaton))
    app.add_handler(CommandHandler("chatoff", cmd_chatoff))

    app.add_handler(CallbackQueryHandler(handle_interval_buttons, pattern=r"^int_\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    restore_jobs(app)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
