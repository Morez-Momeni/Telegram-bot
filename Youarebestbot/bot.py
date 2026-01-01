import os
import random
import sqlite3
import asyncio
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
import uvicorn

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY")
PORT = int(os.getenv("PORT", "10000"))
DB_NAME = os.getenv("DB_PATH", "bot.db")

BB_LLM_PROVIDER = os.getenv("BB_LLM_PROVIDER", "google")
BB_MODEL_NAME = os.getenv("BB_MODEL_NAME", "gemini-2.5-pro")

# ================= CONFIG =================
DEFAULT_INTERVAL = 3600

# ================= MESSAGES =================
MESSAGES = [
    "🌿 با خودت مهربون باش، تو داری بهترینِ خودت رو می‌دی.",
    "💛 امروز فقط دوام آوردن هم موفقیته.",
    "🫶 تو لازم نیست کامل باشی تا ارزشمند باشی.",
    "✨ آروم برو، خودتو له نکن.",
    "🌱 قدم‌های کوچیکت هم پیشرفته.",
    "🧠 با خودت مثل یه دوست حرف بزن.",
    "🍃 نفس عمیق… همه‌چیز قرار نیست همین الان حل بشه.",
    "💧 یه لیوان آب بخور، اینم خوددوستیه.",
    "🌸 تو هنوز در مسیرت هستی، نه عقب.",
]

# ================= KEYBOARDS =================
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
            InlineKeyboardButton("1 دقیقه", callback_data="int_60"),
            InlineKeyboardButton("5 دقیقه", callback_data="int_300"),
        ],
        [
            InlineKeyboardButton("30 دقیقه", callback_data="int_1800"),
            InlineKeyboardButton("1 ساعت", callback_data="int_3600"),
        ],
        [
            InlineKeyboardButton("2 ساعت", callback_data="int_7200"),
            InlineKeyboardButton("3 ساعت", callback_data="int_10800"),
        ],
        [
            InlineKeyboardButton("4 ساعت", callback_data="int_14400"),
        ],
    ]
)

# ================= DATABASE =================
def db():
    return sqlite3.connect(DB_NAME)

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            interval INTEGER DEFAULT 3600,
            is_active INTEGER DEFAULT 0,
            chat_enabled INTEGER DEFAULT 1,
            bb_thread_id TEXT
        )
    """)
    con.commit()
    con.close()

def get_chat(chat_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT interval, is_active, chat_enabled, bb_thread_id FROM chats WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO chats (chat_id) VALUES (?)", (chat_id,))
        con.commit()
        row = (DEFAULT_INTERVAL, 0, 1, None)
    con.close()
    return {
        "interval": row[0],
        "is_active": row[1],
        "chat_enabled": row[2],
        "thread": row[3],
    }

def update_chat(chat_id, **kwargs):
    con = db()
    cur = con.cursor()
    for k, v in kwargs.items():
        cur.execute(f"UPDATE chats SET {k}=? WHERE chat_id=?", (v, chat_id))
    con.commit()
    con.close()

# ================= JOB QUEUE =================
def jobs(app):
    return app.bot_data.setdefault("jobs", {})

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=random.choice(MESSAGES),
    )

def start_job(context, chat_id, interval):
    jm = jobs(context.application)
    if chat_id in jm:
        jm[chat_id].schedule_removal()

    job = context.application.job_queue.run_repeating(
        send_reminder,
        interval=interval,
        first=interval,
        chat_id=chat_id,
    )
    jm[chat_id] = job

def stop_job(context, chat_id):
    jm = jobs(context.application)
    if chat_id in jm:
        jm[chat_id].schedule_removal()
        jm.pop(chat_id)

# ================= BACKBOARD CHAT =================
def bb_client(app):
    if "bb" not in app.bot_data:
        app.bot_data["bb"] = BackboardClient(api_key=BACKBOARD_API_KEY)
    return app.bot_data["bb"]

async def get_thread(app, chat_id):
    chat = get_chat(chat_id)
    if chat["thread"]:
        return chat["thread"]

    client = bb_client(app)
    assistant = await client.create_assistant(
        name="YouAreBestBot",
        description="Persian friendly assistant",
    )
    thread = await client.create_thread(assistant.assistant_id)
    update_chat(chat_id, bb_thread_id=thread.thread_id)
    return thread.thread_id

async def chat_reply(app, chat_id, text):
    if not BACKBOARD_API_KEY:
        return "چت‌بات فعلاً غیرفعاله."

    client = bb_client(app)
    thread_id = await get_thread(app, chat_id)

    try:
        res = await client.add_message(
            thread_id=thread_id,
            content=text,
            llm_provider=BB_LLM_PROVIDER,
            model_name=BB_MODEL_NAME,
            stream=False,
        )
        return res.latest_message.content.strip()[:3500]
    except Exception as e:
        print("Backboard error:", e)
        return "الان نتونستم جواب بدم، بعداً دوباره امتحان کن."

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)

    if chat["is_active"]:
        await update.message.reply_text("⏳ فعاله 😉", reply_markup=main_keyboard)
        return

    start_job(context, chat_id, chat["interval"])
    update_chat(chat_id, is_active=1)

    await update.message.reply_text(
        f"✅ فعال شد!\nهر {chat['interval']//60} دقیقه پیام می‌دم 💛",
        reply_markup=main_keyboard,
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stop_job(context, chat_id)
    update_chat(chat_id, is_active=0)
    await update.message.reply_text("⛔ متوقف شد.", reply_markup=main_keyboard)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(update.effective_chat.id)
    await update.message.reply_text(
        f"📊 وضعیت\n"
        f"{'✅ فعاله' if chat['is_active'] else '❌ خاموشه'}\n"
        f"⏱ فاصله: {chat['interval']//60} دقیقه\n"
        f"💬 چت‌بات: {'روشن' if chat['chat_enabled'] else 'خاموش'}",
        reply_markup=main_keyboard,
    )

async def show_intervals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏱ فاصله زمانی رو انتخاب کن:", reply_markup=interval_keyboard)

async def interval_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    seconds = int(q.data.split("_")[1])
    chat_id = q.message.chat_id

    update_chat(chat_id, interval=seconds)
    chat = get_chat(chat_id)
    if chat["is_active"]:
        start_job(context, chat_id, seconds)

    await q.message.reply_text(
        f"✅ تنظیم شد روی {seconds//60} دقیقه",
        reply_markup=main_keyboard,
    )

async def chat_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_chat(update.effective_chat.id, chat_enabled=1)
    await update.message.reply_text("💬 چت‌بات روشن شد", reply_markup=main_keyboard)

async def chat_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_chat(update.effective_chat.id, chat_enabled=0)
    await update.message.reply_text("🚫 چت‌بات خاموش شد", reply_markup=main_keyboard)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    text = (update.message.text or "").strip()

    if text == "▶️ Start":
        await start(update, context); return
    if text == "⏹ Stop":
        await stop(update, context); return
    if text == "📊 Status":
        await status(update, context); return
    if text == "⏱ Interval":
        await show_intervals(update, context); return
    if text == "💬 Chat On":
        await chat_on(update, context); return
    if text == "🚫 Chat Off":
        await chat_off(update, context); return

    chat = get_chat(chat_id)
    if not chat["chat_enabled"]:
        return

    if chat_type != "private":
        if context.bot.username not in text and not (
            update.message.reply_to_message and
            update.message.reply_to_message.from_user.id == context.bot.id
        ):
            return
        text = text.replace(f"@{context.bot.username}", "").strip()

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = await chat_reply(context.application, chat_id, text)
    await update.message.reply_text(reply)

# ================= WEBHOOK =================
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response("ok")

async def ping(_: Request):
    return PlainTextResponse("pong")

# ================= STARTUP =================
init_db()
application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("stop", stop))
application.add_handler(CommandHandler("status", status))
application.add_handler(CommandHandler("interval", show_intervals))
application.add_handler(CommandHandler("chaton", chat_on))
application.add_handler(CommandHandler("chatoff", chat_off))
application.add_handler(CallbackQueryHandler(interval_cb, pattern=r"^int_"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

async def startup():
    await application.initialize()
    await application.start()

asyncio.get_event_loop().run_until_complete(startup())

starlette_app = Starlette(
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping),
    ]
)

if __name__ == "__main__":
    uvicorn.run(
        starlette_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
