# YouAreBestBot 🤖

A clean, multi-purpose Telegram bot (Persian-first) featuring **prices, holidays, Digikala search**, and an optional **Gemini chatbot mode** — deployable on **Render** via webhook.

<p>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" />
  <img src="https://img.shields.io/badge/Telegram-Bot-26A5E4" />
  <img src="https://img.shields.io/badge/Deploy-Render-8A2BE2" />
  <img src="https://img.shields.io/badge/Webhook-Starlette%20%2B%20Uvicorn-2E8B57" />
  <img src="https://img.shields.io/badge/LLM-Gemini-FF6F00" />
</p>

**Telegram Bot:** `@YOUR_BOT_USERNAME`  
*(Replace with your real bot username)*

---

## Features

###  Gemini Chatbot (Optional)
- Chat mode with lightweight per-user memory
- Exit anytime with “End Chat”
- Friendly handling for common errors (401 / 429 / blocked prompts)

### 📈 Prices & Markets
- 🚗 Car prices (list)
- 💵 FX rates
- 🥇 Gold & coin prices
- ₿ Crypto prices (+ approximate IRR conversion using USD rate)

### 📅 Calendar
- 📅 Today’s events + official holiday status (formatted text)

### 🛒 Digikala Search
- Category browsing: Mobile / Laptop / Apparel
- Manual search
- Prev / Next pagination
- Clean output (no per-item inline buttons)

---

## Tech Stack
- `python-telegram-bot`
- `Starlette + Uvicorn` (Webhook server)
- `httpx` (async API calls)
- Google Gemini (optional chatbot)

---
## Try it
**Telegram Bot:** https://t.me/youarebestbot


