import os
import re
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
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

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
import uvicorn

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "10000"))
BASALAM_API_KEY = os.getenv("BASALAM_API_KEY")

# ================= LOG =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("multi-bot")

# ================= UI =================
main_keyboard = ReplyKeyboardMarkup(
    [
        ["🚗 قیمت خودرو", "💵 قیمت ارز"],
        ["🥇 طلا و سکه", "₿ ارز دیجیتال"],
        ["📅 مناسبت امروز", "🛒 دیجی‌کالا"],
        ["🛍️ با سلام", "ℹ️ راهنما"],
    ],
    resize_keyboard=True,
)

basalam_keyboard = ReplyKeyboardMarkup(
    [
        ["🔎 جستجوی محصولات با سلام", "📋 دسته‌بندی محصولات"],
        ["⬅️ بازگشت", "❌ لغو"],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "🧩 ربات چندکاره\n\n"
    "🚗 قیمت خودرو: لیست کامل قیمت خودروها\n"
    "💵 قیمت ارز: نرخ ارزها\n"
    "🥇 طلا و سکه: طلا/سکه و...\n"
    "₿ ارز دیجیتال: قیمت چند رمزارز (دلاری + تخمینی تومانی)\n"
    "📅 مناسبت امروز: مناسبت‌ها + تعطیلی\n\n"
    "🛒 دیجی‌کالا: جستجو و نمایش محصولات دیجی‌کالا\n"
    "🛍️ با سلام: جستجوی محصولات و دسته‌بندی‌ها در با سلام\n\n"
    "📌 برای استفاده از هر بخش، دکمه‌های مربوطه رو بزن."
)

# ================= API ENDPOINTS =================
# با سلام API Endpoints
BASALAM_BASE = "https://api.basalam.com"
BS_SEARCH_PRODUCTS = f"{BASALAM_BASE}/products/search"
BS_CATEGORIES = f"{BASALAM_BASE}/categories"
BS_PRODUCT_DETAIL = f"{BASALAM_BASE}/products/"

# Digikala API
DIGIKALA_BASE = "https://api.digikala.com/v1"
DK_SEARCH = f"{DIGIKALA_BASE}/search/"
DK_CATEGORY = f"{DIGIKALA_BASE}/categories/{{slug}}/search/"

# ================= HTTP CLIENT =================
_http = None

def _http_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(18.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (TelegramBot)",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://www.digikala.com/",
            },
        )
    return _http

async def http_get_json(url: str, params: dict | None = None):
    c = _http_client()
    r = await c.get(url, params=params)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        txt = r.text.strip()
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw_text": txt}

def chunk_text(text: str, limit: int = 3500):
    parts, cur = [], ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts

# ================= با سلام (Basalam) =================
async def bs_search_products(query: str, page: int = 1) -> str:
    params = {"q": query, "page": page}
    headers = {"Authorization": f"Bearer {BASALAM_API_KEY}"}
    url = BS_SEARCH_PRODUCTS
    data = await http_get_json(url, params=params)

    if not isinstance(data, dict) or not data.get("data"):
        return f"🛍️ نتیجه‌ای برای «{query}» پیدا نشد."

    lines = [f"🛍️ نتایج جستجو برای «{query}» (صفحه {page})\n"]
    for product in data.get("data", [])[:10]:
        title = product.get("title", "بدون عنوان")
        price = product.get("price", "قیمت موجود نیست")
        product_url = product.get("url", "#")
        lines.append(f"• {title}\n  💰 {price}\n  🔗 {product_url}")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"bsp_{query}_{page-1}"))
    nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"bsp_{query}_{page+1}"))
    markup = InlineKeyboardMarkup([nav])
    return "\n".join(lines).strip(), markup

async def bs_categories() -> str:
    url = BS_CATEGORIES
    data = await http_get_json(url)

    if not isinstance(data, dict) or not data.get("data"):
        return "🛍️ دسته‌بندی‌ها در دسترس نیست."

    lines = ["🛍️ دسته‌بندی‌های با سلام:\n"]
    for category in data.get("data", [])[:10]:
        category_name = category.get("name", "بدون نام")
        category_slug = category.get("slug", "")
        lines.append(f"• {category_name} - {category_slug}")
    return "\n".join(lines).strip()

async def bs_product_detail(product_id: str) -> str:
    url = f"{BS_PRODUCT_DETAIL}{product_id}"
    headers = {"Authorization": f"Bearer {BASALAM_API_KEY}"}
    data = await http_get_json(url, params=None)

    if not isinstance(data, dict) or not data.get("data"):
        return "🛍️ جزئیات محصول یافت نشد."

    product = data.get("data")
    title = product.get("title", "بدون عنوان")
    price = product.get("price", "قیمت موجود نیست")
    description = product.get("description", "توضیحاتی برای این محصول موجود نیست.")
    product_url = product.get("url", "#")
    return f"🛍️ جزئیات محصول:\n\n• {title}\n  💰 {price}\n  📋 {description}\n  🔗 {product_url}"

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("سلام 👋 از دکمه‌ها استفاده کن 👇", reply_markup=main_keyboard)
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    context.user_data.pop("dk_in_menu", None)
    await update.message.reply_text("✅ لغو شد.", reply_markup=main_keyboard)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text in ("/help", "ℹ️ راهنما"):
        await help_cmd(update, context); return
    if text == "❌ لغو":
        await cancel_cmd(update, context); return

    awaiting = context.user_data.get("awaiting")
    dk_in_menu = context.user_data.get("dk_in_menu")

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        if text == "🛍️ با سلام":
            context.user_data["dk_in_menu"] = True
            context.user_data.pop("awaiting", None)
            await update.message.reply_text(
                "🛍️ با سلام\nیک دسته انتخاب کن یا «🔎 جستجو» رو بزن.",
                reply_markup=basalam_keyboard,
            )
            return

        # ---- با سلام: جستجوی محصولات ----
        if awaiting == "bs_search_query":
            context.user_data.pop("awaiting", None)
            context.user_data["last_bs_query"] = text
            msg, markup = await bs_search_products(text, page=1)
            await update.message.reply_text(msg, reply_markup=markup or basalam_keyboard)
            return

        # ---- با سلام: دسته‌بندی‌ها ----
        if text == "📋 دسته‌بندی محصولات":
            msg = await bs_categories()
            await update.message.reply_text(msg, reply_markup=basalam_keyboard)
            return

        if text == "🔎 جستجوی محصولات با سلام":
            context.user_data["awaiting"] = "bs_search_query"
            await update.message.reply_text("چی رو جستجو کنم؟", reply_markup=basalam_keyboard)
            return

        if text == "⬅️ بازگشت":
            context.user_data.pop("dk_in_menu", None)
            context.user_data.pop("awaiting", None)
            await update.message.reply_text("برگشتی به منوی اصلی 👇", reply_markup=main_keyboard)
            return

        # ---- جستجو با دکمه‌ها ----
        if text == "🚗 قیمت خودرو":
            out = await feature_cars_all()
            for part in chunk_text(out):
                await update.message.reply_text(part, reply_markup=main_keyboard)
            return

        if text == "💵 قیمت ارز":
            out = await feature_fx()
            await update.message.reply_text(out, reply_markup=main_keyboard)
            return

        if text == "🥇 طلا و سکه":
            out = await feature_gold()
            await update.message.reply_text(out, reply_markup=main_keyboard)
            return

        if text == "₿ ارز دیجیتال":
            out = await feature_crypto()
            await update.message.reply_text(out, reply_markup=main_keyboard)
            return

        if text == "📅 مناسبت امروز":
            out = await feature_today_events()
            await update.message.reply_text(out, reply_markup=main_keyboard)
            return

        await update.message.reply_text("متوجه نشدم 😅 یکی از دکمه‌ها رو بزن یا «ℹ️ راهنما».", reply_markup=main_keyboard)

    except httpx.HTTPError:
        logger.exception("HTTP error")
        await update.message.reply_text("❌ خطا در دریافت اطلاعات از اینترنت. دوباره امتحان کن.", reply_markup=main_keyboard)
    except Exception:
        logger.exception("Unhandled error")
        await update.message.reply_text("❌ یه خطای غیرمنتظره رخ داد. دوباره امتحان کن.", reply_markup=main_keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    try:
        await context.bot.send_chat_action(q.message.chat_id, ChatAction.TYPING)

        # با سلام: صفحه‌بندی نتایج جستجو
        if data.startswith("bsp_"):
            query, page = data.split("_")[1], int(data.split("_")[2])
            msg, markup = await bs_search_products(query, page=page)
            await q.message.reply_text(msg, reply_markup=markup or basalam_keyboard)
            return

        # با سلام: صفحه‌بندی دسته‌ها
        if data.startswith("dkc_"):
            slug, page = data.split("_")[1], int(data.split("_")[2])
            msg, markup = await bs_search_products(slug, page=page)
            await q.message.reply_text(msg, reply_markup=markup or basalam_keyboard)
            return

    except Exception:
        logger.exception("Callback error")
        await q.message.reply_text("❌ خطا. دوباره امتحان کن.", reply_markup=basalam_keyboard)

# ================= TELEGRAM WEBHOOK =================
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(bsp_|dkc_)"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response("ok")

async def ping(_: Request):
    return PlainTextResponse("pong")

@asynccontextmanager
async def lifespan(app: Starlette):
    await application.initialize()
    await application.start()
    logger.info("Bot started")
    yield
    await application.stop()
    await application.shutdown()
    global _http
    if _http:
        await _http.aclose()
        _http = None
    logger.info("Bot stopped")

starlette_app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping, methods=["GET"]),
    ],
)

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TOKEN env var is missing")
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
