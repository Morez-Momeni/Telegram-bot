import os
import random
import sqlite3
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
import uvicorn
import asyncio

# ---------- ENV ----------
TOKEN = os.getenv("TOKEN")
BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY")
PUBLIC_URL = os.getenv("PUBLIC_URL")
PORT = int(os.getenv("PORT", "10000"))
DB_NAME = os.getenv("DB_PATH", "bot.db")

BB_LLM_PROVIDER = os.getenv("BB_LLM_PROVIDER", "google")
BB_MODEL_NAME = os.getenv("BB_MODEL_NAME", "gemini-2.5-pro")

# ---------- DB ----------
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

# ---------- Telegram App ----------
application = ApplicationBuilder().token(TOKEN).build()

# ---------- Simple Handlers (برای تست سالم بودن webhook) ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ ربات زنده‌ست و کار می‌کنه 🎉")

application.add_handler(CommandHandler("start", start))

# ---------- Webhook ----------
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response("ok")

async def ping(_: Request):
    return PlainTextResponse("pong")

# ---------- Starlette App (PORT BINDING) ----------
starlette_app = Starlette(
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping, methods=["GET"]),
    ]
)

# ---------- Main ----------
if __name__ == "__main__":
    if not TOKEN or not PUBLIC_URL:
        raise RuntimeError("Missing TOKEN or PUBLIC_URL")

    init_db()

    async def startup():
        await application.initialize()
        await application.start()

    asyncio.get_event_loop().run_until_complete(startup())

    # ⬅⬅⬅ این خط کلیدی Render است
    uvicorn.run(
        starlette_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
