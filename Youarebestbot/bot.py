import os
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
import uvicorn

# ===== ENV =====
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "10000"))

# ===== Telegram Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ ربات الان کاملاً زنده‌ست و جواب می‌ده 🎉")

# ===== Build Telegram Application =====
tg_app: Application = ApplicationBuilder().token(TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))

# ===== Webhook endpoint =====
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return Response("ok")

async def ping(_: Request):
    return PlainTextResponse("pong")

# ===== Starlette app =====
web_app = Starlette(
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping, methods=["GET"]),
    ]
)

# ===== Startup =====
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TOKEN is not set")

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
