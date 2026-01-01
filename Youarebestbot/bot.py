import os
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
import uvicorn

TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "10000"))

# ---------------- Telegram handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ ربات زنده‌ست و جواب می‌ده")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"echo: {update.message.text}")

# ---------------- Build PTB application ----------------
tg_app: Application = ApplicationBuilder().token(TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

# ---------------- Webhook endpoint ----------------
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return Response("ok")

async def ping(_: Request):
    return PlainTextResponse("pong")

# ---------------- Starlette app ----------------
web_app = Starlette(
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping),
    ]
)

# ---------------- Main ----------------
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TOKEN not set")

    async def startup():
        await tg_app.initialize()
        await tg_app.start()

    asyncio.get_event_loop().run_until_complete(startup())

    uvicorn.run(
        web_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
