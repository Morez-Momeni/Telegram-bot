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

# توکن نرخ (پیشنهاد: داخل ENV بذار)
NERKH_TOKEN = os.getenv("NERKH_TOKEN", "7jJs38mZSFf6uoa6RuNTjByaWGCJgqKlMYxrlMpib5U")

# ================= LOG =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("multi-bot")

# ================= UI =================
main_keyboard = ReplyKeyboardMarkup(
    [
        ["💵 قیمت ارز (نرخ)", "🥇 طلا و سکه (نرخ)"],
        ["₿ کریپتو (نرخ)", "🚗 قیمت خودرو"],
        ["📅 مناسبت امروز", "🛒 جستجوی دیجی‌کالا"],
        ["📱 موبایل دیجی‌کالا", "🧾 محصول دیجی‌کالا با ID"],
        ["ℹ️ راهنما", "❌ لغو"],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "🧩 ربات چندکاره\n\n"
    "💵 قیمت ارز (نرخ): قیمت لحظه‌ای ارزها از سرویس نرخ\n"
    "🥇 طلا و سکه (نرخ): قیمت طلا/سکه از سرویس نرخ\n"
    "₿ کریپتو (نرخ): قیمت رمزارزها از سرویس نرخ\n"
    "🚗 قیمت خودرو: لیست کامل قیمت خودروها\n"
    "📅 مناسبت امروز: مناسبت‌ها + تعطیل رسمی بودن\n\n"
    "🛒 دیجی‌کالا:\n"
    "• «🛒 جستجوی دیجی‌کالا» → بعدش اسم کالا رو بفرست\n"
    "• «📱 موبایل دیجی‌کالا» → لیست موبایل‌ها (صفحه‌بندی)\n"
    "• «🧾 محصول دیجی‌کالا با ID» → بعدش ID عددی محصول رو بفرست\n\n"
    "📌 اگر خروجی طولانی بشه، چند پیام پشت‌سرهم می‌فرستم."
)



NERKH_BASE = "https://api.nerkh.io/v1"
NERKH_CURRENCY_ALL = f"{NERKH_BASE}/prices/json/currency"
NERKH_GOLD_ALL = f"{NERKH_BASE}/prices/json/gold"
NERKH_CRYPTO_ALL = f"{NERKH_BASE}/prices/json/crypto"


CAR_ALL_URL = "https://car.api-sina-free.workers.dev/cars?type=all"


HOLIDAY_URL = "https://holidayapi.ir/jalali/{y}/{m}/{d}"


DIGIKALA_BASE = "https://api.digikala.com/v1"
DK_SEARCH = f"{DIGIKALA_BASE}/search/"
DK_MOBILE_CAT = f"{DIGIKALA_BASE}/categories/mobile-phone/search/"
DK_PRODUCT = f"{DIGIKALA_BASE}/product/{{pid}}/"

_http: httpx.AsyncClient | None = None

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

async def http_get_json(url: str, params: dict | None = None, headers: dict | None = None):
    c = _http_client()
    r = await c.get(url, params=params, headers=headers)
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

def deep_get(d, keys: list[str], default=None):
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )

    jy += 33 * (days // 12053)
    days %= 12053

    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)

    return jy, jm, jd


def nerkh_headers():
    # طبق نمونه‌ها: Authorization: Bearer <TOKEN>
    return {"Authorization": f"Bearer {NERKH_TOKEN}"}

def normalize_nerkh_list(payload) -> list[dict]:
    """
    چون ساختار دقیق ممکنه تغییر کنه، تلاش می‌کنیم لیست آیتم‌ها رو از چند مسیر پیدا کنیم.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for path in (["data"], ["result"], ["items"], ["prices"], ["data", "items"]):
        v = deep_get(payload, path, None)
        if isinstance(v, list):
            return v
    # اگر dict از نماد به آبجکت بود
    if all(isinstance(v, dict) for v in payload.values()) and len(payload) > 0:
        out = []
        for k, v in payload.items():
            v2 = dict(v)
            v2.setdefault("symbol", k)
            out.append(v2)
        return out
    return []

def format_nerkh_item(it: dict) -> str:
    # کلیدهای رایج احتمالی
    name = it.get("name_fa") or it.get("name") or it.get("title") or it.get("symbol") or "—"
    symbol = it.get("symbol") or it.get("code") or ""
    price = it.get("price") or it.get("value") or it.get("latest") or it.get("rate") or ""
    unit = it.get("unit") or it.get("currency") or "تومان"
    # بعضی‌ها price عددی هست
    if isinstance(price, (int, float)):
        price_txt = f"{int(price):,}"
    else:
        price_txt = str(price).strip()
    if symbol and symbol not in str(name):
        head = f"{name} ({symbol})"
    else:
        head = f"{name}"
    if price_txt:
        return f"• {head}: {price_txt} {unit}".strip()
    return f"• {head}"

async def feature_nerkh_currency() -> str:
    data = await http_get_json(NERKH_CURRENCY_ALL, headers=nerkh_headers())
    items = normalize_nerkh_list(data)
    if not items:
        return "💵 خطا در دریافت ارز از نرخ (خروجی خالی/مسدود)."
    lines = ["💵 قیمت ارز (نرخ)\n"]
    for it in items[:60]:
        lines.append(format_nerkh_item(it))
    if len(items) > 60:
        lines.append("\n(لیست کامل خیلی طولانی بود؛ بخشی نمایش داده شد.)")
    return "\n".join(lines).strip()

async def feature_nerkh_gold() -> str:
    data = await http_get_json(NERKH_GOLD_ALL, headers=nerkh_headers())
    items = normalize_nerkh_list(data)
    if not items:
        return "🥇 خطا در دریافت طلا/سکه از نرخ (خروجی خالی/مسدود)."
    lines = ["🥇 طلا و سکه (نرخ)\n"]
    for it in items[:80]:
        lines.append(format_nerkh_item(it))
    if len(items) > 80:
        lines.append("\n(لیست کامل خیلی طولانی بود؛ بخشی نمایش داده شد.)")
    return "\n".join(lines).strip()

async def feature_nerkh_crypto() -> str:
    data = await http_get_json(NERKH_CRYPTO_ALL, headers=nerkh_headers())
    items = normalize_nerkh_list(data)
    if not items:
        return "₿ خطا در دریافت کریپتو از نرخ (خروجی خالی/مسدود)."
    lines = ["₿ ارز دیجیتال (نرخ)\n"]
    for it in items[:60]:
        lines.append(format_nerkh_item(it))
    if len(items) > 60:
        lines.append("\n(لیست کامل خیلی طولانی بود؛ بخشی نمایش داده شد.)")
    return "\n".join(lines).strip()


async def feature_cars_all() -> str:
    data = await http_get_json(CAR_ALL_URL)
    cars = data.get("cars") if isinstance(data, dict) else None
    if not cars:
        return "🚗 الان نتونستم لیست قیمت خودرو رو بگیرم. (خروجی خالی بود)"
    lines = ["🚗 قیمت خودرو (همه)\n"]
    for i, c in enumerate(cars, start=1):
        brand = (c.get("brand") or "").strip()
        name = (c.get("name") or "").strip()
        market = (c.get("market_price") or "").strip()
        factory = (c.get("factory_price") or "").strip()
        title = f"{i}. {brand} - {name}".strip(" -")
        lines.append(title)
        if market:
            lines.append(f"   بازار: {market}")
        if factory and factory != "0":
            lines.append(f"   کارخانه: {factory}")
        lines.append("")
    return "\n".join(lines).strip()

async def feature_today_events() -> str:
    now = datetime.now(timezone.utc)
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    url = HOLIDAY_URL.format(y=jy, m=jm, d=jd)
    data = await http_get_json(url)
    if not isinstance(data, dict):
        return "📅 الان نتونستم مناسبت امروز رو بگیرم."
    date_text = data.get("date") or f"{jy}/{jm:02d}/{jd:02d}"
    is_holiday = data.get("is_holiday")
    events = data.get("events") or []
    lines = [f"📅 مناسبت‌های امروز ({date_text})"]
    if is_holiday is True:
        lines.append("✅ امروز تعطیل رسمی است.")
    elif is_holiday is False:
        lines.append("❌ امروز تعطیل رسمی نیست.")
    else:
        lines.append("ℹ️ وضعیت تعطیلی مشخص نیست.")

    if isinstance(events, list) and events:
        lines.append("\n🟣 مناسبت‌ها:")
        for ev in events[:25]:
            if isinstance(ev, dict):
                title = ev.get("title") or ev.get("description") or ev.get("event") or str(ev)
            else:
                title = str(ev)
            title = title.strip()
            if title:
                lines.append(f"• {title}")
    else:
        lines.append("\n(مناسبتی ثبت نشده)")
    return "\n".join(lines).strip()


async def dk_get_json(url: str, params: dict | None = None):
    try:
        return await http_get_json(url, params=params)
    except Exception:
        if url.endswith("/"):
            return await http_get_json(url[:-1], params=params)
        return await http_get_json(url + "/", params=params)

def dk_extract_products(payload: dict) -> list[dict]:
    for path in (["data", "products"], ["data", "search", "products"], ["data", "items"], ["products"]):
        v = deep_get(payload, path, None)
        if isinstance(v, list):
            return v
    return []

def dk_price_text(prod: dict) -> str:
    dv = prod.get("default_variant") if isinstance(prod, dict) else None
    if isinstance(dv, dict):
        price = dv.get("price") or {}
        if isinstance(price, dict):
            sp = price.get("selling_price")
            rp = price.get("rrp_price")
            dp = price.get("discount_percent")
            parts = []
            if sp is not None:
                parts.append(f"💰 {sp:,} تومان" if isinstance(sp, int) else f"💰 {sp} تومان")
            if dp:
                parts.append(f"🔻 {dp}%")
            if rp and rp != sp:
                parts.append(f"(قبل: {rp:,})" if isinstance(rp, int) else f"(قبل: {rp})")
            if parts:
                return " ".join(parts)
    return "—"

async def feature_dk_search(query: str, page: int = 1) -> tuple[str, InlineKeyboardMarkup | None]:
    payload = await dk_get_json(DK_SEARCH, params={"q": query, "page": page})
    prods = dk_extract_products(payload)
    if not prods:
        return "🛒 نتیجه‌ای پیدا نشد. یه عبارت دیگه امتحان کن.", None

    lines = [f"🛒 نتایج دیجی‌کالا برای: «{query}» (صفحه {page})\n"]
    buttons = []
    for p in prods[:10]:
        pid = p.get("id") or p.get("dkp_id") or p.get("product_id")
        title = p.get("title_fa") or p.get("title") or p.get("name") or "بدون عنوان"
        title = str(title).strip()
        price = dk_price_text(p)
        if pid:
            lines.append(f"• {title}\n  {price}\n  🆔 {pid}\n")
            buttons.append([InlineKeyboardButton(f"🧾 {title[:22]}", callback_data=f"dkp_{pid}")])
        else:
            lines.append(f"• {title}\n  {price}\n")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"dks_{page-1}"))
    nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"dks_{page+1}"))
    buttons.append(nav)

    return "\n".join(lines).strip(), InlineKeyboardMarkup(buttons)

async def feature_dk_mobile(page: int = 1) -> tuple[str, InlineKeyboardMarkup | None]:
    payload = await dk_get_json(DK_MOBILE_CAT, params={"page": page})
    prods = dk_extract_products(payload)
    if not prods:
        return "📱 فعلاً لیست موبایل‌ها نیومد. دوباره تلاش کن.", None

    lines = [f"📱 موبایل‌های دیجی‌کالا (صفحه {page})\n"]
    buttons = []
    for p in prods[:10]:
        pid = p.get("id") or p.get("product_id")
        title = p.get("title_fa") or p.get("title") or p.get("name") or "بدون عنوان"
        price = dk_price_text(p)
        if pid:
            lines.append(f"• {title}\n  {price}\n  🆔 {pid}\n")
            buttons.append([InlineKeyboardButton(f"🧾 {str(title)[:22]}", callback_data=f"dkp_{pid}")])
        else:
            lines.append(f"• {title}\n  {price}\n")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"dkm_{page-1}"))
    nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"dkm_{page+1}"))
    buttons.append(nav)

    return "\n".join(lines).strip(), InlineKeyboardMarkup(buttons)

async def feature_dk_product(pid: str) -> str:
    payload = await dk_get_json(DK_PRODUCT.format(pid=pid))
    prod = deep_get(payload, ["data", "product"], None) or deep_get(payload, ["data"], None) or payload
    if not isinstance(prod, dict):
        return "🧾 جزئیات محصول دریافت نشد."

    title = prod.get("title_fa") or prod.get("title") or prod.get("name") or "بدون عنوان"
    url = prod.get("url") or prod.get("share_url") or ""
    rating = deep_get(prod, ["rating", "rate"], None) or prod.get("rating") or None
    price = dk_price_text(prod)

    if price == "—":
        dv = deep_get(payload, ["data", "product", "default_variant"], None)
        if isinstance(dv, dict):
            price = dk_price_text({"default_variant": dv})

    lines = [f"🧾 جزئیات محصول", f"🆔 {pid}", f"📦 {title}", f"{price}"]
    if rating:
        lines.append(f"⭐ امتیاز: {rating}")
    if url:
        lines.append(f"🔗 لینک: {url}")
    return "\n".join(lines).strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("سلام 👋 از دکمه‌ها استفاده کن 👇", reply_markup=main_keyboard)
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    await update.message.reply_text("✅ لغو شد. از دکمه‌ها استفاده کن.", reply_markup=main_keyboard)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text in ("/help", "ℹ️ راهنما"):
        await help_cmd(update, context); return
    if text == "❌ لغو":
        await cancel_cmd(update, context); return

    awaiting = context.user_data.get("awaiting")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        # حالت‌های ورودی دیجی‌کالا
        if awaiting == "dk_search_query":
            context.user_data.pop("awaiting", None)
            context.user_data["last_dk_query"] = text
            msg, markup = await feature_dk_search(text, page=1)
            await update.message.reply_text(msg, reply_markup=markup or main_keyboard)
            return

        if awaiting == "dk_product_id":
            context.user_data.pop("awaiting", None)
            pid = re.sub(r"[^\d]", "", text)
            if not pid:
                await update.message.reply_text("🧾 لطفاً فقط ID عددی بفرست.", reply_markup=main_keyboard)
                return
            msg = await feature_dk_product(pid)
            await update.message.reply_text(msg, reply_markup=main_keyboard)
            return

        # دکمه‌ها
        if text == "💵 قیمت ارز (نرخ)":
            out = await feature_nerkh_currency()
            for part in chunk_text(out):
                await update.message.reply_text(part, reply_markup=main_keyboard)
            return

        if text == "🥇 طلا و سکه (نرخ)":
            out = await feature_nerkh_gold()
            for part in chunk_text(out):
                await update.message.reply_text(part, reply_markup=main_keyboard)
            return

        if text == "₿ کریپتو (نرخ)":
            out = await feature_nerkh_crypto()
            for part in chunk_text(out):
                await update.message.reply_text(part, reply_markup=main_keyboard)
            return

        if text == "🚗 قیمت خودرو":
            out = await feature_cars_all()
            for part in chunk_text(out):
                await update.message.reply_text(part, reply_markup=main_keyboard)
            return

        if text == "📅 مناسبت امروز":
            out = await feature_today_events()
            await update.message.reply_text(out, reply_markup=main_keyboard)
            return

        if text == "🛒 جستجوی دیجی‌کالا":
            context.user_data["awaiting"] = "dk_search_query"
            await update.message.reply_text("چی رو تو دیجی‌کالا سرچ کنم؟ (مثلاً: آیفون 13)", reply_markup=main_keyboard)
            return

        if text == "📱 موبایل دیجی‌کالا":
            msg, markup = await feature_dk_mobile(page=1)
            await update.message.reply_text(msg, reply_markup=markup or main_keyboard)
            return

        if text == "🧾 محصول دیجی‌کالا با ID":
            context.user_data["awaiting"] = "dk_product_id"
            await update.message.reply_text("ID محصول رو بفرست (فقط عدد). مثال: 6850997", reply_markup=main_keyboard)
            return

        await update.message.reply_text("متوجه نشدم 😅 یکی از دکمه‌ها رو بزن یا «ℹ️ راهنما».", reply_markup=main_keyboard)

    except httpx.HTTPError:
        logger.exception("HTTP error")
        await update.message.reply_text(
            "❌ خطا در دریافت اطلاعات.\n"
            "اگر مشکل از «نرخ» بود، احتمالاً به خاطر محدودیت IP غیرایران است.",
            reply_markup=main_keyboard
        )
    except Exception:
        logger.exception("Unhandled error")
        await update.message.reply_text("❌ یه خطای غیرمنتظره رخ داد. دوباره امتحان کن.", reply_markup=main_keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    try:
        await context.bot.send_chat_action(q.message.chat_id, ChatAction.TYPING)

        if data.startswith("dkp_"):
            pid = data.split("_", 1)[1]
            msg = await feature_dk_product(pid)
            await q.message.reply_text(msg, reply_markup=main_keyboard)
            return

        if data.startswith("dks_"):
            page = int(data.split("_", 1)[1])
            last_q = context.user_data.get("last_dk_query")
            if not last_q:
                await q.message.reply_text("برای صفحه‌بندی، دوباره «🛒 جستجوی دیجی‌کالا» رو بزن.", reply_markup=main_keyboard)
                return
            msg, markup = await feature_dk_search(last_q, page=page)
            await q.message.reply_text(msg, reply_markup=markup or main_keyboard)
            return

        if data.startswith("dkm_"):
            page = int(data.split("_", 1)[1])
            msg, markup = await feature_dk_mobile(page=page)
            await q.message.reply_text(msg, reply_markup=markup or main_keyboard)
            return

    except Exception:
        logger.exception("Callback error")
        await q.message.reply_text("❌ خطا. دوباره امتحان کن.", reply_markup=main_keyboard)

# ================= TELEGRAM WEBHOOK =================
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(dkp_|dks_|dkm_)"))
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
