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


TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "10000"))

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()  # پیشنهاد: gemini-2.5-flash


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("multi-bot")


main_keyboard = ReplyKeyboardMarkup(
    [
        ["🚗 قیمت خودرو", "💵 قیمت ارز"],
        ["🥇 طلا و سکه", "₿ ارز دیجیتال"],
        ["📅 مناسبت امروز", "🛒 دیجی‌کالا"],
        ["💬 چت‌بات", "ℹ️ راهنما"],
    ],
    resize_keyboard=True,
)

digikala_menu_keyboard = ReplyKeyboardMarkup(
    [
        ["📱 موبایل دیجی‌کالا", "💻 لپ‌تاپ دیجی‌کالا"],
        ["👕 پوشاک دیجی‌کالا", "🔎 سرچ دستی دیجی‌کالا"],
        ["⬅️ بازگشت", "❌ لغو"],
    ],
    resize_keyboard=True,
)

chat_keyboard = ReplyKeyboardMarkup(
    [
        ["🛑 پایان چت", "⬅️ بازگشت"],
        ["ℹ️ راهنما"],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "🧩 ربات چندکاره\n\n"
    "🚗 قیمت خودرو: لیست قیمت خودروها\n"
    "💵 قیمت ارز: نرخ ارزها\n"
    "🥇 طلا و سکه: قیمت طلا و سکه\n"
    "₿ ارز دیجیتال: قیمت رمزارزها\n"
    "📅 مناسبت امروز: مناسبت‌ها و تعطیلی رسمی\n\n"
    "🛒 دیجی‌کالا:\n"
    "• «🛒 دیجی‌کالا» → انتخاب دسته یا سرچ دستی\n"
    "• نتایج: فقط متن + دکمه قبلی/بعدی\n\n"
    "💬 چت‌بات:\n"
    "• شروع گفتگو آزاد\n"
    "• خروج از چت: «🛑 پایان چت»\n"
)


CAR_ALL_URL = "https://car.api-sina-free.workers.dev/cars?type=all"

CODEBAZAN_ARZ_URL = "https://api.codebazan.ir/arz/?type=arz"
CODEBAZAN_TALA_URL = "https://api.codebazan.ir/arz/?type=tala"

COINLORE = "https://api.coinlore.net/api/tickers/?start=0&limit=15"

HOLIDAY_URL = "https://holidayapi.ir/jalali/{y}/{m}/{d}"

# Digikala
DIGIKALA_BASE = "https://api.digikala.com/v1"
DK_SEARCH = f"{DIGIKALA_BASE}/search/"
DK_CATEGORY = f"{DIGIKALA_BASE}/categories/{{slug}}/search/"
DIGIKALA_CATS = {
    "📱 موبایل دیجی‌کالا": ("mobile-phone", "موبایل"),
    "💻 لپ‌تاپ دیجی‌کالا": ("notebook-netbook-ultrabook", "لپ‌تاپ"),
    "👕 پوشاک دیجی‌کالا": ("apparel", "پوشاک"),
}

# Gemini REST
GEMINI_URL = lambda model: f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_http: httpx.AsyncClient | None = None

def _http_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(25.0, connect=10.0),
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
    try:
        r = await c.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else None
        body = ""
        try:
            body = e.response.text[:800] if e.response else ""
        except Exception:
            pass
        return {"_error": True, "status_code": status, "url": url, "body": body}
    except Exception as e:
        return {"_error": True, "status_code": None, "url": url, "body": str(e)}

async def http_post_json(url: str, json_body: dict, headers: dict | None = None):
    c = _http_client()
    try:
        r = await c.post(url, json=json_body, headers=headers)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else None
        body = ""
        try:
            body = e.response.text[:1200] if e.response else ""
        except Exception:
            pass
        return {"_error": True, "status_code": status, "url": url, "body": body}
    except Exception as e:
        return {"_error": True, "status_code": None, "url": url, "body": str(e)}

def chunk_text(text: str, limit: int = 3500):
    parts, cur = [], ""
    for line in (text or "").splitlines(True):
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts

def deep_get(d, keys: list, default=None):
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            return default
    return cur

def to_int_from_price_str(s: str) -> int | None:
    if not s:
        return None
    s2 = re.sub(r"[^\d]", "", str(s))
    return int(s2) if s2.isdigit() else None

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

def gemini_headers():
    if not GEMINI_API_KEY:
        return None
    return {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }

def gemini_extract_text(resp: dict) -> str | None:
    # candidates[0].content.parts[*].text
    parts = deep_get(resp, ["candidates", 0, "content", "parts"], [])
    if isinstance(parts, list):
        texts = []
        for p in parts:
            t = (p or {}).get("text")
            if t:
                texts.append(str(t))
        out = "\n".join(texts).strip()
        return out if out else None
    return None

async def gemini_chat(history: list[dict], user_text: str) -> str:
    if not GEMINI_API_KEY:
        return "❌ GEMINI_API_KEY تنظیم نشده. تو Render → Environment بذارش."

    history = (history or [])[-12:]

    contents = history + [{"role": "user", "parts": [{"text": user_text}]}]

    payload = {
        "systemInstruction": {  # طبق API reference :contentReference[oaicite:3]{index=3}
            "parts": [{"text": "تو یک دستیار فارسیِ مودب و کوتاه‌گو هستی. پاسخ‌ها را روشن، کاربردی و خلاصه بده."}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 512,
        },
    }

    data = await http_post_json(GEMINI_URL(GEMINI_MODEL), payload, headers=gemini_headers())

    if isinstance(data, dict) and data.get("_error"):
        sc = data.get("status_code")
        body = str(data.get("body", ""))[:500]
        if sc == 401:
            return "❌ خطای 401: کلید Gemini اشتباهه یا دسترسی نداره."
        if sc == 429:
            return "⏳ الان محدودیت درخواست خوردی (429). چند لحظه بعد دوباره امتحان کن."
        return f"❌ خطا از Gemini (HTTP {sc}): {body}"

    # اگر پرامپت بلاک بشه ممکنه candidates نده و promptFeedback بیاد
    block_reason = deep_get(data, ["promptFeedback", "blockReason"], None)
    if block_reason and not deep_get(data, ["candidates"], None):
        return "⚠️ درخواست به خاطر قوانین ایمنی Gemini بلاک شد. یه جور دیگه بپرس."

    txt = gemini_extract_text(data)
    return txt or "❌ پاسخی از Gemini نگرفتم. دوباره بفرست."

async def feature_fx() -> str:
    data = await http_get_json(CODEBAZAN_ARZ_URL)
    items = data.get("Result") if isinstance(data, dict) else None
    if not items or (isinstance(data, dict) and data.get("_error")):
        return "💵 الان نتونستم قیمت ارز رو بگیرم."
    lines = ["💵 قیمت ارز (منتخب)\n"]
    for it in items[:30]:
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        if name and price:
            lines.append(f"• {name}: {price}")
    return "\n".join(lines).strip()

async def feature_gold() -> str:
    data = await http_get_json(CODEBAZAN_TALA_URL)
    items = data.get("Result") if isinstance(data, dict) else None
    if not items or (isinstance(data, dict) and data.get("_error")):
        return "🥇 الان نتونستم طلا و سکه رو بگیرم."
    lines = ["🥇 طلا و سکه (منتخب)\n"]
    for it in items[:35]:
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        if name and price:
            lines.append(f"• {name}: {price}")
    return "\n".join(lines).strip()

async def get_usd_toman_rate() -> int | None:
    data = await http_get_json(CODEBAZAN_ARZ_URL)
    items = data.get("Result") if isinstance(data, dict) else []
    for it in items or []:
        if (it.get("name") or "").strip() == "دلار":
            return to_int_from_price_str(it.get("price"))
    return None

async def feature_crypto() -> str:
    data = await http_get_json(COINLORE)
    coins = data.get("data") if isinstance(data, dict) else None
    if not coins or (isinstance(data, dict) and data.get("_error")):
        return "₿ الان نتونستم قیمت ارز دیجیتال رو بگیرم."
    usd_toman = await get_usd_toman_rate()
    lines = ["₿ ارز دیجیتال (۱۵ کوین اول)\n"]
    for c in coins[:15]:
        name = c.get("name") or c.get("symbol") or "?"
        symbol = (c.get("symbol") or "").upper()
        price_usd = c.get("price_usd")
        line = f"• {name} ({symbol}) — ${price_usd}"
        if usd_toman:
            try:
                p_tm = int(float(price_usd) * usd_toman)
                line += f" ≈ {p_tm:,} تومان"
            except Exception:
                pass
        lines.append(line)
    return "\n".join(lines).strip()

async def feature_cars_all() -> str:
    data = await http_get_json(CAR_ALL_URL)
    cars = data.get("cars") if isinstance(data, dict) else None
    if not cars or (isinstance(data, dict) and data.get("_error")):
        return "🚗 الان نتونستم لیست قیمت خودرو رو بگیرم."
    lines = ["🚗 قیمت خودرو (بخشی از لیست)\n"]
    for i, c in enumerate(cars[:80], start=1):
        brand = (c.get("brand") or "").strip()
        name = (c.get("name") or "").strip()
        market = (c.get("market_price") or "").strip()
        lines.append(f"{i}. {brand} {name} — بازار: {market}")
    if len(cars) > 80:
        lines.append("\n(لیست خیلی طولانی بود؛ بخشی نمایش داده شد.)")
    return "\n".join(lines).strip()

async def feature_today_events() -> str:
    now = datetime.now(timezone.utc)
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    url = HOLIDAY_URL.format(y=jy, m=jm, d=jd)
    data = await http_get_json(url)
    if isinstance(data, dict) and data.get("_error"):
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
            dp = price.get("discount_percent")
            if sp is not None:
                s = f"{sp:,} تومان" if isinstance(sp, int) else f"{sp} تومان"
                if dp:
                    s += f" 🔻{dp}%"
                return s
    return "—"

async def dk_search(query: str, page: int = 1):
    payload = await http_get_json(DK_SEARCH, params={"q": query, "page": page})
    if isinstance(payload, dict) and payload.get("_error"):
        return "🛒 دیجی‌کالا الان پاسخ نداد.", None

    prods = dk_extract_products(payload)
    if not prods:
        return f"🛒 نتیجه‌ای برای «{query}» پیدا نشد.", None

    lines = [f"🛒 دیجی‌کالا | جستجو: «{query}» | صفحه {page}\n"]
    for p in prods[:12]:
        title = p.get("title_fa") or p.get("title") or p.get("name") or "بدون عنوان"
        price = dk_price_text(p)
        lines.append(f"• {str(title).strip()}\n  💰 {price}\n")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"dks_{page-1}"))
    nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"dks_{page+1}"))
    markup = InlineKeyboardMarkup([nav])
    return "\n".join(lines).strip(), markup

async def dk_category(slug: str, title_fa: str, page: int = 1):
    url = DK_CATEGORY.format(slug=slug)
    payload = await http_get_json(url, params={"page": page})
    if isinstance(payload, dict) and payload.get("_error"):
        return "🛒 دیجی‌کالا الان پاسخ نداد.", None

    prods = dk_extract_products(payload)
    if not prods:
        return f"🛒 دیجی‌کالا | {title_fa}\nنتیجه‌ای پیدا نشد.", None

    lines = [f"🛒 دیجی‌کالا | دسته: {title_fa} | صفحه {page}\n"]
    for p in prods[:12]:
        title = p.get("title_fa") or p.get("title") or p.get("name") or "بدون عنوان"
        price = dk_price_text(p)
        lines.append(f"• {str(title).strip()}\n  💰 {price}\n")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"dkc_{slug}_{page-1}"))
    nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"dkc_{slug}_{page+1}"))
    markup = InlineKeyboardMarkup([nav])
    return "\n".join(lines).strip(), markup

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("سلام 👋 از دکمه‌ها استفاده کن 👇", reply_markup=main_keyboard)
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    if text in ("/help", "ℹ️ راهنما"):
        await help_cmd(update, context)
        return

    if text == "⬅️ بازگشت":
        context.user_data.clear()
        await update.message.reply_text("برگشتی به منوی اصلی 👇", reply_markup=main_keyboard)
        return

    if text == "❌ لغو":
        context.user_data.clear()
        await update.message.reply_text("✅ لغو شد.", reply_markup=main_keyboard)
        return

    if text == "🛑 پایان چت":
        context.user_data.pop("chat_mode", None)
        context.user_data.pop("gemini_history", None)
        await update.message.reply_text("✅ چت بسته شد.", reply_markup=main_keyboard)
        return

    if context.user_data.get("chat_mode") is True:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        history = context.user_data.get("gemini_history") or []
        out = await gemini_chat(history, text)
        # ذخیره تاریخچه
        history = (history or [])[-12:]
        history.append({"role": "user", "parts": [{"text": text}]})
        history.append({"role": "model", "parts": [{"text": out}]})
        context.user_data["gemini_history"] = history[-12:]

        for part in chunk_text(out):
            await update.message.reply_text(part, reply_markup=chat_keyboard)
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    try:
        if text == "💬 چت‌بات":
            context.user_data["chat_mode"] = True
            context.user_data["gemini_history"] = []
            await update.message.reply_text(
                "💬 چت‌بات Gemini فعال شد.\nبرای خروج: « پایان چت»",
                reply_markup=chat_keyboard,
            )
            return

       
        if text == "🛒 دیجی‌کالا":
            context.user_data["mode"] = "digikala"
            context.user_data.pop("awaiting", None)
            await update.message.reply_text("🛒 دیجی‌کالا: دسته یا سرچ دستی", reply_markup=digikala_menu_keyboard)
            return

        if text == "🔎 سرچ دستی دیجی‌کالا":
            context.user_data["mode"] = "digikala"
            context.user_data["awaiting"] = "dk_search_query"
            await update.message.reply_text("چی رو تو دیجی‌کالا سرچ کنم؟", reply_markup=digikala_menu_keyboard)
            return

        if context.user_data.get("awaiting") == "dk_search_query":
            context.user_data.pop("awaiting", None)
            context.user_data["dk_last_query"] = text
            msg, markup = await dk_search(text, page=1)
            await update.message.reply_text(msg, reply_markup=markup or digikala_menu_keyboard)
            return

        if text in DIGIKALA_CATS:
            slug, fa_title = DIGIKALA_CATS[text]
            context.user_data["dk_last_cat"] = (slug, fa_title)
            msg, markup = await dk_category(slug, fa_title, page=1)
            await update.message.reply_text(msg, reply_markup=markup or digikala_menu_keyboard)
            return

        if text == "💵 قیمت ارز":
            out = await feature_fx()
        elif text == "🥇 طلا و سکه":
            out = await feature_gold()
        elif text == "₿ ارز دیجیتال":
            out = await feature_crypto()
        elif text == "🚗 قیمت خودرو":
            out = await feature_cars_all()
        elif text == "📅 مناسبت امروز":
            out = await feature_today_events()
        elif text == "ℹ️ راهنما":
            out = HELP_TEXT
        else:
            out = "متوجه نشدم 😅 یکی از دکمه‌ها رو بزن یا «ℹ️ راهنما»."

        for part in chunk_text(out):
            await update.message.reply_text(part, reply_markup=main_keyboard)

    except Exception:
        logger.exception("Unhandled error")
        await update.message.reply_text("❌ یه خطا رخ داد. لاگ رو چک کن.", reply_markup=main_keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    try:
        await context.bot.send_chat_action(q.message.chat_id, ChatAction.TYPING)

        if data.startswith("dks_"):
            page = int(data.split("_", 1)[1])
            last_q = context.user_data.get("dk_last_query")
            if not last_q:
                await q.message.reply_text("اول سرچ دستی دیجی‌کالا رو انجام بده.", reply_markup=digikala_menu_keyboard)
                return
            msg, markup = await dk_search(last_q, page=page)
            await q.message.reply_text(msg, reply_markup=markup or digikala_menu_keyboard)
            return

        if data.startswith("dkc_"):
            _, slug, page_s = data.split("_", 2)
            page = int(page_s)
            last = context.user_data.get("dk_last_cat")
            fa_title = last[1] if last else slug
            msg, markup = await dk_category(slug, fa_title, page=page)
            await q.message.reply_text(msg, reply_markup=markup or digikala_menu_keyboard)
            return

        await q.message.reply_text("❌ دکمه نامعتبر", reply_markup=main_keyboard)

    except Exception:
        logger.exception("Callback error")
        await q.message.reply_text("❌ خطا در صفحه‌بندی.", reply_markup=main_keyboard)

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(dks_|dkc_)"))
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


