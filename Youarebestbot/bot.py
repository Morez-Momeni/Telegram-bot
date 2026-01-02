import os
import re
import json
import time
import httpx
import jdatetime

from telegram import Update
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

# ================= API ENDPOINTS =================
CODEBAZAN_ARZ_URL = "https://api.codebazan.ir/arz/?type=arz"
CODEBAZAN_TALA_URL = "https://api.codebazan.ir/arz/?type=tala"
CODEBAZAN_CAR_URL = "https://api.codebazan.ir/car-price/Result.php"

HOLIDAY_URL_TEMPLATE = "https://holidayapi.ir/jalali/{y}/{m}/{d}"
HAFEZ_URL = "https://hafez-dxle.onrender.com/fal"

NOBITEX_STATS_URL = "https://apiv2.nobitex.ir/market/stats"

# ================= Helpers =================
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("ي", "ی").replace("ك", "ک")
    s = re.sub(r"\s+", " ", s)
    return s

def _to_int_price(s: str):
    if not s:
        return None
    # "1,356,800" -> 1356800
    s = re.sub(r"[^\d]", "", s)
    return int(s) if s.isdigit() else None

def _fmt_int(n: int) -> str:
    return f"{n:,}"

def _ua_headers():
    return {"User-Agent": "Mozilla/5.0 (TelegramBot; +https://t.me/)"}  # ساده ولی موثر

def http_client(app):
    # یک کلاینت مشترک برای کل اپ (بهتر از ساختن در هر درخواست)
    c = app.bot_data.get("http")
    if c is None:
        app.bot_data["http"] = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=8.0),
            headers=_ua_headers(),
            follow_redirects=True,
        )
        c = app.bot_data["http"]
    return c

class TTLCache:
    def __init__(self):
        self.data = None
        self.exp = 0

    def get(self):
        return self.data if time.time() < self.exp else None

    def set(self, data, ttl=60):
        self.data = data
        self.exp = time.time() + ttl

async def fetch_json(app, url, params=None):
    c = http_client(app)
    r = await c.get(url, params=params)
    r.raise_for_status()
    return r.json()

async def fetch_text(app, url, params=None):
    c = http_client(app)
    r = await c.get(url, params=params)
    r.raise_for_status()
    return r.text

# ================= Simple HTML table parser (بدون bs4) =================
def parse_first_html_table(html: str):
    """
    خروجی: list[dict] با کلیدهای ستون‌ها
    این parser خیلی ساده است و برای جدول‌های معمولی جواب می‌دهد.
    """
    # هدرها
    thead = re.search(r"<thead.*?</thead>", html, flags=re.S | re.I)
    tbody = re.search(r"<tbody.*?</tbody>", html, flags=re.S | re.I)
    if not tbody:
        # بعضی صفحات tbody ندارند
        tbody = re.search(r"<table.*?</table>", html, flags=re.S | re.I)

    if not tbody:
        return []

    header_cells = []
    if thead:
        header_cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", thead.group(0), flags=re.S | re.I)
    else:
        # اگر thead نبود، اولین tr را هدر فرض کن
        first_tr = re.search(r"<tr[^>]*>.*?</tr>", tbody.group(0), flags=re.S | re.I)
        if first_tr:
            header_cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", first_tr.group(0), flags=re.S | re.I)

    headers = [re.sub(r"<[^>]+>", "", h).strip() for h in header_cells if h.strip()]
    if not headers:
        # fallback
        headers = ["col1", "col2", "col3", "col4", "col5"]

    rows = []
    trs = re.findall(r"<tr[^>]*>.*?</tr>", tbody.group(0), flags=re.S | re.I)
    for tr in trs[1:] if thead is None and len(trs) > 0 else trs:
        tds = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S | re.I)
        cols = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
        if len(cols) < 2:
            continue
        row = {}
        for i, v in enumerate(cols):
            k = headers[i] if i < len(headers) else f"col{i+1}"
            row[_norm(k)] = v
        rows.append(row)
    return rows

# ================= Caches =================
ARZ_CACHE = TTLCache()
TALA_CACHE = TTLCache()
CAR_CACHE = TTLCache()

COMMON_FX = ["دلار", "یورو", "پوند انگلیس", "درهم امارات", "لیر ترکیه"]
COMMON_GOLD = ["طلای 18 عیار / 750", "مثقال طلا", "طلای ۲۴ عیار"]

FX_CODE_MAP = {
    "usd": "دلار",
    "eur": "یورو",
    "gbp": "پوند انگلیس",
    "aed": "درهم امارات",
    "try": "لیر ترکیه",
}

CRYPTO_MAP = {
    "btc": "btc",
    "eth": "eth",
    "usdt": "usdt",
    "xrp": "xrp",
    "doge": "doge",
    "ada": "ada",
}

# ================= Commands =================
HELP_TEXT = """🧩 ربات چندکاره (API-based)

دستورها:
💱 /arz [نام یا کد]  → قیمت ارز (مثلاً: /arz دلار  |  /arz usd)
🪙 /tala [کلمه]      → قیمت طلا/سکه و ...
🚗 /khodro [نام]     → قیمت خودرو (مثلاً: /khodro پژو 207)
📿 /fal              → فال حافظ
🗓️ /holiday [YYYY/MM/DD] → مناسبت‌های آن روز (پیش‌فرض: امروز)
🕒 /now              → تاریخ امروز (شمسی + میلادی)
₿ /crypto [symbol] [dst]  → آمار بازار نوبیتکس (مثلاً: /crypto btc rls)

نمونه‌ها:
- /arz usd
- /tala 18
- /khodro دنا
- /holiday 1404/10/12
- /crypto btc rls
"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g = jdatetime.datetime.now().togregorian()
    j = jdatetime.datetime.now()
    await update.message.reply_text(
        f"🕒 الان\n"
        f"شمسی: {j.strftime('%Y/%m/%d %H:%M')}\n"
        f"میلادی: {g.strftime('%Y-%m-%d %H:%M')}"
    )

async def arz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    qn = _norm(q)
    if qn in FX_CODE_MAP:
        q = FX_CODE_MAP[qn]
        qn = _norm(q)

    app = context.application
    data = ARZ_CACHE.get()
    if data is None:
        try:
            data = await fetch_json(app, CODEBAZAN_ARZ_URL)
            ARZ_CACHE.set(data, ttl=60)
        except Exception:
            await update.message.reply_text("❌ خطا در دریافت قیمت ارز. دوباره امتحان کن.")
            return

    items = data.get("Result") or []
    if not q:
        # نمایش چند مورد معروف
        out = ["💱 قیمت ارز (چند مورد رایج):"]
        for name in COMMON_FX:
            it = next((x for x in items if _norm(x.get("name")) == _norm(name)), None)
            if it:
                p = it.get("price", "-")
                out.append(f"• {it.get('name')}: {p}")
        out.append("\nبرای جستجو: /arz دلار یا /arz usd")
        await update.message.reply_text("\n".join(out))
        return

    # جستجو
    matches = [x for x in items if qn in _norm(x.get("name"))]
    if not matches:
        await update.message.reply_text("🔎 چیزی پیدا نکردم. یه نام دیگه بزن (مثلاً: دلار، یورو، پوند).")
        return

    out = ["💱 نتیجه:"]
    for it in matches[:12]:
        out.append(f"• {it.get('name')}: {it.get('price','-')}")
    await update.message.reply_text("\n".join(out))

async def tala_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    qn = _norm(q)

    app = context.application
    data = TALA_CACHE.get()
    if data is None:
        try:
            data = await fetch_json(app, CODEBAZAN_TALA_URL)
            TALA_CACHE.set(data, ttl=60)
        except Exception:
            await update.message.reply_text("❌ خطا در دریافت قیمت طلا. دوباره امتحان کن.")
            return

    items = data.get("Result") or []
    if not q:
        out = ["🪙 قیمت طلا (چند مورد رایج):"]
        for name in COMMON_GOLD:
            it = next((x for x in items if _norm(x.get("name")) == _norm(name)), None)
            if it:
                out.append(f"• {it.get('name')}: {it.get('price','-')}")
        out.append("\nبرای جستجو: /tala مثقال یا /tala 18")
        await update.message.reply_text("\n".join(out))
        return

    matches = [x for x in items if qn in _norm(x.get("name"))]
    if not matches:
        await update.message.reply_text("🔎 چیزی پیدا نکردم. مثلا: /tala 18 یا /tala سکه")
        return

    out = ["🪙 نتیجه:"]
    for it in matches[:12]:
        out.append(f"• {it.get('name')}: {it.get('price','-')}")
    await update.message.reply_text("\n".join(out))

async def khodro_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    qn = _norm(q)

    if not q:
        await update.message.reply_text("🚗 اسم خودرو رو بده. مثال: /khodro پژو 207")
        return

    app = context.application
    rows = CAR_CACHE.get()
    if rows is None:
        try:
            html = await fetch_text(app, CODEBAZAN_CAR_URL)
            rows = parse_first_html_table(html)
            CAR_CACHE.set(rows, ttl=180)  # کمی بیشتر
        except Exception:
            await update.message.reply_text("❌ خطا در دریافت قیمت خودرو. دوباره امتحان کن.")
            return

    # تلاش برای پیدا کردن ستون نام/مدل
    # چون ساختار دقیق جدول ممکنه تغییر کنه، چند کلید احتمالی رو چک می‌کنیم
    def row_name(r):
        for k in ["خودرو", "نام", "مدل", "title", "name", "col1"]:
            kn = _norm(k)
            if kn in r and r.get(kn):
                return r.get(kn)
        # fallback: اولین مقدار
        return next(iter(r.values()), "")

    matches = [r for r in rows if qn in _norm(row_name(r))]
    if not matches:
        await update.message.reply_text("🔎 چیزی پیدا نکردم. یه اسم کوتاه‌تر امتحان کن (مثلاً: 207، دنا، تارا).")
        return

    out = ["🚗 نتیجه (چند مورد):"]
    for r in matches[:8]:
        name = row_name(r)

        # سعی می‌کنیم چند ستون معروف رو هم نمایش بدیم
        # اگر نبود، چند مقدار اول رو می‌ریزیم بیرون
        known = []
        for k in ["قیمت کارخانه", "قیمت بازار", "بازار", "کارخانه", "price", "col2", "col3", "col4"]:
            kn = _norm(k)
            if kn in r and r.get(kn):
                known.append(f"{k}: {r.get(kn)}")

        if not known:
            vals = list(r.values())[:4]
            known = [f"اطلاعات: {' | '.join(vals)}"]

        out.append(f"• {name}\n  " + "  |  ".join(known))

    await update.message.reply_text("\n".join(out))

async def fal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    try:
        data = await fetch_json(app, HAFEZ_URL)
        title = data.get("title", "فال حافظ")
        content = data.get("content") or ""
        interp = data.get("interpreter") or ""

        msg = f"📿 {title}\n\n{content}\n\n📝 تعبیر:\n{interp}"
        # تلگرام محدودیت طول دارد
        await update.message.reply_text(msg[:3900])
    except Exception:
        await update.message.reply_text("❌ فال دریافت نشد. دوباره امتحان کن.")

async def holiday_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = " ".join(context.args).strip()
    if arg:
        m = re.match(r"^(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})$", arg)
        if not m:
            await update.message.reply_text("فرمت درست: /holiday 1404/10/12")
            return
        y, mo, d = map(int, m.groups())
    else:
        today = jdatetime.date.today()
        y, mo, d = today.year, today.month, today.day

    url = HOLIDAY_URL_TEMPLATE.format(y=y, m=mo, d=d)
    app = context.application

    try:
        data = await fetch_json(app, url)
    except Exception:
        await update.message.reply_text("❌ خطا در دریافت مناسبت‌ها. دوباره امتحان کن.")
        return

    # چون ساختار پاسخ ممکنه فرق کنه، چند حالت رو پوشش می‌دیم:
    # - لیست holiday/events
    # - یا متن/فیلدهای ساده
    out = [f"🗓️ مناسبت‌های {y}/{mo:02d}/{d:02d}"]

    if isinstance(data, dict):
        # رایج: events/holidays
        for key in ["events", "holidays", "occasion", "occasions"]:
            v = data.get(key)
            if isinstance(v, list) and v:
                for e in v[:15]:
                    if isinstance(e, dict):
                        title = e.get("title") or e.get("name") or e.get("event") or json.dumps(e, ensure_ascii=False)
                        out.append(f"• {title}")
                    else:
                        out.append(f"• {str(e)}")
                break
        else:
            # fallback: هر چی هست خلاصه
            # اگر is_holiday داشت:
            if "is_holiday" in data:
                out.append(f"تعطیل رسمی: {'✅' if data.get('is_holiday') else '❌'}")
            # اگر متن داشت:
            for k in ["description", "text", "day", "month", "weekday"]:
                if k in data and data.get(k):
                    out.append(f"{k}: {data.get(k)}")
    else:
        out.append(str(data))

    await update.message.reply_text("\n".join(out)[:3900])

async def crypto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /crypto btc rls
    args = context.args
    if not args:
        await update.message.reply_text("مثال: /crypto btc rls")
        return

    src = _norm(args[0])
    src = CRYPTO_MAP.get(src, src)
    dst = _norm(args[1]) if len(args) > 1 else "rls"

    app = context.application
    try:
        data = await fetch_json(app, NOBITEX_STATS_URL, params={"srcCurrency": src, "dstCurrency": dst})
    except Exception:
        await update.message.reply_text("❌ خطا در دریافت قیمت کریپتو از نوبیتکس.")
        return

    if not isinstance(data, dict) or data.get("status") != "ok":
        await update.message.reply_text("❌ پاسخ نامعتبر از نوبیتکس.")
        return

    stats = data.get("stats") or {}
    # کلیدها شبیه btc-rls
    key = f"{src}-{dst}"
    row = stats.get(key)
    if not row:
        # اگر نبود، اولین مورد را نشان بده
        if stats:
            key, row = next(iter(stats.items()))
        else:
            await update.message.reply_text("❌ داده‌ای برنگشت.")
            return

    latest = row.get("latest")
    day_change = row.get("dayChange")
    day_low = row.get("dayLow")
    day_high = row.get("dayHigh")

    msg = (
        f"₿ {key}\n"
        f"آخرین قیمت: {latest}\n"
        f"تغییر ۲۴ساعت: {day_change}%\n"
        f"کمترین/بیشترین: {day_low} / {day_high}"
    )
    await update.message.reply_text(msg)

async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هر متن غیر-کامند => راهنما
    await update.message.reply_text("برای دیدن دستورات: /help")

# ================= Webhook =================
application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("now", now_cmd))
application.add_handler(CommandHandler("arz", arz_cmd))
application.add_handler(CommandHandler("tala", tala_cmd))
application.add_handler(CommandHandler("khodro", khodro_cmd))
application.add_handler(CommandHandler("fal", fal_cmd))
application.add_handler(CommandHandler("holiday", holiday_cmd))
application.add_handler(CommandHandler("crypto", crypto_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))

async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response("ok")

async def ping(_: Request):
    return PlainTextResponse("pong")

async def on_startup():
    await application.initialize()
    await application.start()

async def on_shutdown():
    # بستن http client
    c = application.bot_data.get("http")
    if c:
        await c.aclose()
    await application.stop()
    await application.shutdown()

starlette_app = Starlette(
    routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/ping", ping),
    ],
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TOKEN env var is missing")

    uvicorn.run(
        starlette_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
