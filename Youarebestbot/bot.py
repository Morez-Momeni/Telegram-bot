import os
import re
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
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

# ================= LOG =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("multi-bot")

# ================= UI (KEYBOARD) =================
main_keyboard = ReplyKeyboardMarkup(
    [
        ["🚗 قیمت خودرو", "💵 قیمت ارز"],
        ["🥇 طلا و سکه", "₿ ارز دیجیتال"],
        ["📅 مناسبت امروز", "🌙 فال حافظ"],
        ["ℹ️ راهنما"],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "👋 سلام!\n"
    "من یه ربات چندکاره‌ام. از دکمه‌ها استفاده کن:\n\n"
    "🚗 قیمت خودرو: لیست کامل قیمت خودروها (بازار/کارخانه)\n"
    "💵 قیمت ارز: نرخ ارزهای رایج\n"
    "🥇 طلا و سکه: طلا، مثقال، سکه و...\n"
    "₿ ارز دیجیتال: قیمت چند رمزارز (دلاری + تخمینی تومانی)\n"
    "📅 مناسبت امروز: مناسبت‌ها و تعطیلی امروز\n"
    "🌙 فال حافظ: یک فال\n\n"
    "📌 نکته: اگر خروجی خیلی طولانی باشه، چند پیام پشت سر هم می‌فرستم."
)

# ================= HTTP (shared client) =================
_http: httpx.AsyncClient | None = None

async def http_get_json(url: str, timeout: float = 15.0):
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r = await _http.get(url)
    r.raise_for_status()
    # بعضی API ها Content-Type درست ندارن، پس محکم‌کاری:
    try:
        return r.json()
    except Exception:
        # تلاش برای parse متن
        txt = r.text.strip()
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw_text": txt}

def chunk_text(text: str, limit: int = 3500):
    """تلگرام 4096 محدودیت داره؛ ما امن‌تر 3500 می‌فرستیم."""
    parts = []
    cur = ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts

def to_int_from_price_str(s: str) -> int | None:
    if not s:
        return None
    s2 = re.sub(r"[^\d]", "", str(s))
    return int(s2) if s2.isdigit() else None

# ================= JALALI CONVERSION (no extra libs) =================
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

# ================= FEATURES =================
async def feature_hafez() -> str:
    data = await http_get_json("https://hafez-dxle.onrender.com/fal")
    if "_raw_text" in data:
        return f"🌙 فال حافظ\n\n{data['_raw_text']}".strip()

    # سعی می‌کنیم چند حالت رایج رو پوشش بدیم
    if isinstance(data, dict):
        title = data.get("title") or data.get("نام") or "فال حافظ"
        poem = data.get("poem") or data.get("fal") or data.get("text") or data.get("شعر") or ""
        interp = data.get("interpretation") or data.get("tafsir") or data.get("تعبیر") or ""
        out = f"🌙 {title}\n\n"
        if poem:
            out += f"{poem}\n"
        if interp:
            out += f"\n🟡 تعبیر:\n{interp}\n"
        return out.strip()

    # اگر لیست بود
    return f"🌙 فال حافظ\n\n{str(data)[:3500]}"

async def feature_cars_all() -> str:
    # API بدون کلید: type=all
    data = await http_get_json("https://car.api-sina-free.workers.dev/cars?type=all")
    cars = []
    if isinstance(data, dict):
        cars = data.get("cars") or []

    if not cars:
        return "🚗 الان نتونستم لیست قیمت خودرو رو بگیرم. (خروجی خالی بود)"

    lines = []
    lines.append("🚗 قیمت خودرو (همه)\n")
    for i, c in enumerate(cars, start=1):
        brand = (c.get("brand") or "").strip()
        name = (c.get("name") or "").strip()
        market = (c.get("market_price") or "").strip()
        factory = (c.get("factory_price") or "").strip()
        chg = (c.get("change_percent") or "").strip()
        chv = (c.get("change_value") or "").strip()

        title = f"{i}. {brand} - {name}".strip(" -")
        lines.append(title)
        if market:
            lines.append(f"   بازار: {market}")
        if factory and factory != "0":
            lines.append(f"   کارخانه: {factory}")
        if chg or chv:
            lines.append(f"   تغییر: {chv} ({chg})".strip())
        lines.append("")  # blank line

    return "\n".join(lines).strip()

async def feature_fx() -> str:
    data = await http_get_json("https://api.codebazan.ir/arz/?type=arz")
    items = []
    if isinstance(data, dict):
        items = data.get("Result") or []

    if not items:
        return "💵 الان نتونستم قیمت ارز رو بگیرم."

    # چند ارز مهم رو اول نشون بده
    priority = {"دلار", "یورو", "پوند انگلیس", "درهم امارات", "لیر ترکیه", "دلار کانادا"}
    first = [x for x in items if (x.get("name") or "").strip() in priority]
    rest = [x for x in items if x not in first]
    show = first + rest[:20]  # خیلی طولانی نشه

    lines = ["💵 قیمت ارز (نمونه‌ی مهم‌ها + چند مورد دیگر)\n"]
    for it in show:
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        if name and price:
            lines.append(f"• {name}: {price}")
    lines.append("\n📌 برای دیدن همه ارزها، بهم بگو «همه ارزها» (به صورت متن طولانی می‌فرستم).")
    return "\n".join(lines).strip()

async def feature_fx_all() -> str:
    data = await http_get_json("https://api.codebazan.ir/arz/?type=arz")
    items = (data.get("Result") or []) if isinstance(data, dict) else []
    if not items:
        return "💵 الان نتونستم قیمت ارز رو بگیرم."
    lines = ["💵 قیمت ارز (همه)\n"]
    for it in items:
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        if name and price:
            lines.append(f"• {name}: {price}")
    return "\n".join(lines).strip()

async def feature_gold() -> str:
    data = await http_get_json("https://api.codebazan.ir/arz/?type=tala")
    items = []
    if isinstance(data, dict):
        items = data.get("Result") or []
    if not items:
        return "🥇 الان نتونستم طلا و سکه رو بگیرم."

    # فقط موارد مهم‌تر رو اول نشون بده
    priority_keys = ["طلای 18 عیار", "طلای ۲۴ عیار", "مثقال", "سکه", "ربع", "نیم"]
    def score(name: str):
        return sum(1 for k in priority_keys if k in name)

    items_sorted = sorted(items, key=lambda x: score((x.get("name") or "")), reverse=True)

    lines = ["🥇 طلا و سکه (منتخب)\n"]
    for it in items_sorted[:25]:
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        if name and price:
            lines.append(f"• {name}: {price}")

    lines.append("\n📌 اگر «همه طلا» بگی، کل لیست رو می‌فرستم.")
    return "\n".join(lines).strip()

async def feature_gold_all() -> str:
    data = await http_get_json("https://api.codebazan.ir/arz/?type=tala")
    items = (data.get("Result") or []) if isinstance(data, dict) else []
    if not items:
        return "🥇 الان نتونستم طلا و سکه رو بگیرم."
    lines = ["🥇 طلا و سکه (همه)\n"]
    for it in items:
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        if name and price:
            lines.append(f"• {name}: {price}")
    return "\n".join(lines).strip()

async def get_usd_toman_rate() -> int | None:
    data = await http_get_json("https://api.codebazan.ir/arz/?type=arz")
    items = (data.get("Result") or []) if isinstance(data, dict) else []
    for it in items:
        if (it.get("name") or "").strip() == "دلار":
            return to_int_from_price_str(it.get("price"))
    return None

async def feature_crypto() -> str:
    # CoinLore: بدون کلید
    data = await http_get_json("https://api.coinlore.net/api/tickers/?start=0&limit=15")
    usd_toman = await get_usd_toman_rate()  # از همین ربات می‌گیریم
    coins = []
    if isinstance(data, dict):
        coins = data.get("data") or []

    if not coins:
        return "₿ الان نتونستم قیمت ارز دیجیتال رو بگیرم."

    lines = ["₿ ارز دیجیتال (۱۵ کوین اول)\n"]
    if usd_toman:
        lines.append(f"نرخ دلار مبنا (تقریبی): {usd_toman:,} تومان\n")
    else:
        lines.append("نرخ دلار مبنا پیدا نشد؛ فقط قیمت دلاری نمایش داده می‌شود.\n")

    for c in coins:
        name = c.get("name") or c.get("symbol") or "?"
        symbol = (c.get("symbol") or "").upper()
        price_usd = c.get("price_usd")
        try:
            p_usd = float(price_usd)
        except Exception:
            p_usd = None

        line = f"• {name} ({symbol}) — ${price_usd}"
        if usd_toman and p_usd is not None:
            p_tm = int(p_usd * usd_toman)
            line += f" ≈ {p_tm:,} تومان"
        lines.append(line)

    return "\n".join(lines).strip()

async def feature_today_events() -> str:
    # تاریخ امروز (UTC) -> برای ایران مناسبت روز، بهتره local باشه؛ ولی چون API جلالی می‌خواد،
    # تاریخ سیستم رو می‌گیریم. Render معمولاً UTC هست. برای ساده‌سازی همین رو می‌گیریم:
    now = datetime.now(timezone.utc)
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)

    url = f"https://holidayapi.ir/jalali/{jy}/{jm}/{jd}"
    data = await http_get_json(url)

    if not isinstance(data, dict):
        return "📅 الان نتونستم مناسبت امروز رو بگیرم."

    # ساخت متن خوشگل:
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

    if events and isinstance(events, list):
        lines.append("\n🟣 مناسبت‌ها:")
        for ev in events:
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

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\nاز دکمه‌ها استفاده کن 👇",
        reply_markup=main_keyboard,
    )
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=main_keyboard)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # دکمه‌ها
    if text in ("ℹ️ راهنما", "/help"):
        await help_cmd(update, context)
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        if text == "🌙 فال حافظ":
            out = await feature_hafez()

        elif text == "🚗 قیمت خودرو":
            out = await feature_cars_all()

        elif text == "💵 قیمت ارز":
            out = await feature_fx()

        elif text == "🥇 طلا و سکه":
            out = await feature_gold()

        elif text == "₿ ارز دیجیتال":
            out = await feature_crypto()

        elif text == "📅 مناسبت امروز":
            out = await feature_today_events()

        # چند عبارت کمکی برای «همه»
        elif text == "همه ارزها":
            out = await feature_fx_all()

        elif text == "همه طلا":
            out = await feature_gold_all()

        else:
            out = (
                "متوجه نشدم چی می‌خوای 😅\n"
                "از دکمه‌ها استفاده کن یا «ℹ️ راهنما» رو بزن."
            )

        # ارسال با تکه‌تکه کردن
        for part in chunk_text(out):
            await update.message.reply_text(part, reply_markup=main_keyboard)

    except httpx.HTTPError as e:
        logger.exception("HTTP error")
        await update.message.reply_text("❌ خطا در دریافت اطلاعات از اینترنت. دوباره امتحان کن.", reply_markup=main_keyboard)
    except Exception as e:
        logger.exception("Unhandled error")
        await update.message.reply_text("❌ یه خطای غیرمنتظره رخ داد. دوباره امتحان کن.", reply_markup=main_keyboard)

# ================= TELEGRAM WEBHOOK =================
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
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
    # init bot
    await application.initialize()
    await application.start()
    logger.info("Bot started")
    yield
    # shutdown
    await application.stop()
    await application.shutdown()
    if _http:
        await _http.aclose()
    logger.info("Bot stopped")

starlette_app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping, methods=["GET"]),
    ],
)

if __name__ == "__main__":
    uvicorn.run(
        starlette_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
