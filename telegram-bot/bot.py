"""
Muhammed Fashion Store — Shopping Bot with Admin Panel & Order System
=====================================================================
python-telegram-bot v20+  |  Google Gemini AI  |  products.json catalog

Customer flow
─────────────
  /start  → store banner + main menu (photo message, edited in-place)
  Browse Products → category grid → product carousel (◀ ▶)
  Order Now → guided checkout (name → phone → address → qty → notes → confirm)
  Contact Us → contact details

Order flow
──────────
  Tap "🛒 Order Now" on any product
  → Full Name → Phone Number → Delivery Address → Quantity → Notes (optional)
  → Order summary shown for confirmation
  → On confirm: order saved to orders.json, admin notified, customer gets receipt

Admin panel  (owner only, gated by ADMIN_CHAT_ID)
──────────────────────────────────────────────────
  /admin → Admin Main Menu
    ├── 📦 Manage Products  (add / edit / delete)
    └── 🏪 Store Settings   (name / Gmail / WhatsApp)

All catalog changes are written to products.json and hot-reloaded immediately.
All orders are appended to orders.json.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types as genai_types
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Catalog — loaded from products.json; hot-reloadable
# ─────────────────────────────────────────────────────────────────────────────
PRODUCTS_FILE = Path(__file__).parent / "products.json"

STORE:   dict = {}
CATS:    list = []
CAT_MAP: dict = {}


def load_catalog() -> dict:
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def reload_catalog() -> None:
    global STORE, CATS, CAT_MAP
    data    = load_catalog()
    STORE   = data["store"]
    CATS    = data["categories"]
    CAT_MAP = {c["id"]: c for c in CATS}
    logger.info(
        "Catalog reloaded: %d categories, %d products",
        len(CATS),
        sum(len(c["products"]) for c in CATS),
    )


def save_catalog() -> None:
    data = {"store": STORE, "categories": CATS}
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    reload_catalog()
    logger.info("Catalog saved to %s", PRODUCTS_FILE)


reload_catalog()   # initial load at startup

# ─────────────────────────────────────────────────────────────────────────────
# Orders — persisted to orders.json
# ─────────────────────────────────────────────────────────────────────────────
ORDERS_FILE = Path(__file__).parent / "orders.json"


def load_orders() -> dict:
    if not ORDERS_FILE.exists():
        return {"orders": []}
    with open(ORDERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_order(order: dict) -> str:
    """Append one order to orders.json and return its order_id."""
    data = load_orders()
    data["orders"].append(order)
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Order saved: %s", order["order_id"])
    return order["order_id"]


def _new_order_id() -> str:
    return f"ORD-{int(time.time())}"

# ─────────────────────────────────────────────────────────────────────────────
# Gemini AI client
# ─────────────────────────────────────────────────────────────────────────────
gemini_client = genai.Client()
GEMINI_MODEL  = "gemini-2.0-flash-lite"


def build_system_prompt() -> str:
    return (
        f"You are a friendly shopping assistant for {STORE['name']}, "
        "a premium Nigerian fashion and lifestyle store. "
        "Help customers with product questions, sizing, availability, and delivery. "
        "Be concise and warm. "
        f"For orders, direct customers to {STORE.get('contact_email', 'our support team')} "
        f"or WhatsApp {STORE.get('contact_whatsapp', '')}."
    )

# ─────────────────────────────────────────────────────────────────────────────
# Admin config & notifications
# ─────────────────────────────────────────────────────────────────────────────
_raw = os.environ.get("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID: int | None = int(_raw) if _raw.lstrip("-").isdigit() else None

if ADMIN_CHAT_ID:
    logger.info("Admin panel enabled for chat ID %d", ADMIN_CHAT_ID)
else:
    logger.warning("ADMIN_CHAT_ID not set — admin panel and order alerts are disabled.")


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ADMIN_CHAT_ID and user.id == ADMIN_CHAT_ID)


async def notify_admin(
    context: ContextTypes.DEFAULT_TYPE, user, action: str
) -> None:
    if not ADMIN_CHAT_ID:
        return
    if user and user.id == ADMIN_CHAT_ID:
        return
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Unknown"
    username  = f"@{user.username}" if user.username else "_(no username)_"
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"🔔 *New interaction*\n\n"
                f"👤 *Name:* {full_name}\n"
                f"🆔 *User ID:* `{user.id}`\n"
                f"📎 *Username:* {username}\n"
                f"💬 *Action:* {action}"
            ),
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Admin notification failed: %s", exc)


async def send_order_to_admin(context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    """Send a fully formatted new-order card to the admin."""
    if not ADMIN_CHAT_ID:
        return
    c   = order["customer"]
    p   = order["product"]
    qty = order["quantity"]
    notes_line = f"\n💬 *Notes:* {order['notes']}" if order.get("notes") else ""
    text = (
        f"🆕 *NEW ORDER — {order['order_id']}*\n"
        f"🕐 {order['timestamp']}\n"
        f"{'─' * 28}\n\n"
        f"🛍️ *Product:* {p['name']}\n"
        f"📂 *Category:* {p['category']}\n"
        f"🔢 *Quantity:* {qty}\n"
        f"💰 *Unit Price:* {_fmt_price(p['unit_price'])}\n"
        f"💵 *Total:* {_fmt_price(order['total_price'])}\n"
        f"{'─' * 28}\n\n"
        f"👤 *Customer:* {c['full_name']}\n"
        f"📞 *Phone:* {c['phone']}\n"
        f"🏠 *Address:* {c['address']}\n"
        f"🆔 *Telegram:* {c['telegram_username']} (`{c['telegram_id']}`)"
        f"{notes_line}"
    )
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown"
        )
    except Exception as exc:
        logger.error("Failed to send order to admin: %s", exc)

# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler state constants
# ─────────────────────────────────────────────────────────────────────────────

# Admin panel states  (0 – 15)
(
    ADMIN_MAIN,
    ADD_CAT, ADD_NAME, ADD_DESC, ADD_PRICE, ADD_PHOTO,
    EDIT_CAT, EDIT_PROD, EDIT_FIELD, EDIT_VAL, EDIT_PHOTO,
    DEL_CAT, DEL_PROD, DEL_CONFIRM,
    SETTINGS_FIELD, SETTINGS_VAL,
) = range(16)

# Order flow states  (100 – 105)  — no overlap with admin states
(
    ORD_NAME,
    ORD_PHONE,
    ORD_ADDRESS,
    ORD_QTY,
    ORD_NOTES,
    ORD_CONFIRM,
) = range(100, 106)

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_price(price: int) -> str:
    return f"₦{price:,}"


def _escape_md(text: str) -> str:
    """Escape characters that break Telegram Markdown v1 parsing.

    User-supplied values (names, addresses, notes …) must be escaped before
    being interpolated into a parse_mode='Markdown' string, otherwise any
    underscore, asterisk, back-tick or bracket in the text causes Telegram to
    return a BadRequest error, which python-telegram-bot catches silently —
    leaving the user with no reply and the conversation frozen.
    """
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _new_prod_id(cat_id: str) -> str:
    return f"{cat_id}_{int(time.time())}"

# ─────────────────────────────────────────────────────────────────────────────
# ── CUSTOMER UI  ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
        [InlineKeyboardButton("📞 Contact Us",       callback_data="contact")],
    ])


def cats_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row:  list[InlineKeyboardButton]       = []
    for cat in CATS:
        row.append(InlineKeyboardButton(
            f"{cat['emoji']} {cat['name']}", callback_data=f"cat:{cat['id']}"
        ))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def product_keyboard(cat_id: str, idx: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if total > 1:
        rows.append([
            InlineKeyboardButton("◀ Prev", callback_data=f"nav:{cat_id}:{(idx-1)%total}"),
            InlineKeyboardButton("Next ▶", callback_data=f"nav:{cat_id}:{(idx+1)%total}"),
        ])
    rows.append([InlineKeyboardButton("🛒 Order Now", callback_data=f"ord:{cat_id}:{idx}")])
    rows.append([
        InlineKeyboardButton("🔙 Categories", callback_data="cats"),
        InlineKeyboardButton("🏠 Home",       callback_data="home"),
    ])
    return InlineKeyboardMarkup(rows)


def contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
        [InlineKeyboardButton("🏠 Home",             callback_data="home")],
    ])


def after_order_keyboard(cat_id: str, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Continue Shopping", callback_data="cats")],
        [InlineKeyboardButton("🏠 Home",               callback_data="home")],
    ])


def home_caption() -> str:
    return (
        f"🛍️ *Welcome to {STORE['name']}!*\n\n"
        f"_{STORE['tagline']}_\n\n"
        "Browse our collection of premium fashion and lifestyle products. "
        "Tap a button below to get started 👇"
    )


def cats_caption() -> str:
    lines = "\n".join(f"  {c['emoji']} {c['name']}" for c in CATS)
    return f"📂 *Shop by Category*\n\n{lines}\n\nChoose a category to browse 👇"


def product_caption(cat: dict, product: dict, idx: int) -> str:
    total = len(cat["products"])
    return (
        f"*{product['name']}*\n\n"
        f"{product['description']}\n\n"
        f"💰 *Price: {_fmt_price(product['price'])}*\n\n"
        f"_{cat['emoji']} {cat['name']}  •  {idx + 1} of {total}_"
    )


def contact_caption() -> str:
    email    = STORE.get("contact_email", "")
    whatsapp = STORE.get("contact_whatsapp", "")
    hours    = STORE.get("business_hours", "Mon – Fri, 9 AM – 6 PM")
    lines    = [f"📞 *Contact {STORE['name']}*\n"]
    if email:    lines.append(f"📧 *Email:* {email}")
    if whatsapp: lines.append(f"💬 *WhatsApp:* {whatsapp}")
    lines += [
        f"\n🕐 *Hours:* {hours}",
        "\nWe typically respond within *2 business hours*.",
        "Include the product name and your delivery address for faster service.",
    ]
    return "\n".join(lines)


async def _render_photo(
    q, photo_url: str, caption: str, keyboard: InlineKeyboardMarkup
) -> None:
    """Edit the catalog message in-place, or replace it cleanly if it was plain text."""
    media = InputMediaPhoto(media=photo_url, caption=caption, parse_mode="Markdown")
    if q.message.photo:
        await q.edit_message_media(media=media, reply_markup=keyboard)
    else:
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.chat.send_photo(
            photo=photo_url, caption=caption,
            parse_mode="Markdown", reply_markup=keyboard,
        )

# ─────────────────────────────────────────────────────────────────────────────
# ── CUSTOMER COMMAND HANDLERS ─────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("/start from %s (%s)", user.first_name if user else "?", user.id if user else "?")
    await notify_admin(context, user, "/start — opened the store")
    await update.message.reply_photo(
        photo=STORE["banner_image"],
        caption=home_caption(),
        parse_mode="Markdown",
        reply_markup=home_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await notify_admin(context, user, "/help")
    await update.message.reply_text(
        f"ℹ️ *{STORE['name']} — Help*\n\n"
        "Commands:\n"
        "/start  — Open the store\n"
        "/help   — Show this message\n"
        "/myid   — Show your Telegram user ID\n"
        "/cancel — Cancel the current order form\n\n"
        "Use the buttons to browse and order. "
        "Or just *type a question* and our AI will answer it 🤖",
        parse_mode="Markdown",
        reply_markup=home_keyboard(),
    )


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"🆔 Your numeric Telegram ID:\n\n`{update.effective_chat.id}`",
        parse_mode="Markdown",
    )

# ─────────────────────────────────────────────────────────────────────────────
# ── CUSTOMER CALLBACK HANDLERS ───────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await notify_admin(context, q.from_user, "🏠 Home")
    await _render_photo(q, STORE["banner_image"], home_caption(), home_keyboard())


async def cb_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await notify_admin(context, q.from_user, "📂 Browse categories")
    await _render_photo(q, STORE["banner_image"], cats_caption(), cats_keyboard())


async def cb_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await notify_admin(context, q.from_user, "📞 Contact Us")
    await _render_photo(q, STORE["banner_image"], contact_caption(), contact_keyboard())


async def cb_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q      = update.callback_query
    cat_id = q.data.split(":", 1)[1]
    cat    = CAT_MAP.get(cat_id)
    if not cat or not cat["products"]:
        await q.answer("No products in this category yet.", show_alert=True); return
    await q.answer()
    await notify_admin(context, q.from_user, f"Browsed: {cat['emoji']} {cat['name']}")
    p = cat["products"][0]
    await _render_photo(q, p["image_url"], product_caption(cat, p, 0),
                        product_keyboard(cat_id, 0, len(cat["products"])))


async def cb_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q              = update.callback_query
    _, cat_id, raw = q.data.split(":")
    idx            = int(raw)
    cat            = CAT_MAP.get(cat_id)
    if not cat:
        await q.answer("Category not found.", show_alert=True); return
    products = cat["products"]
    idx      = idx % len(products)
    p        = products[idx]
    await q.answer()
    await notify_admin(context, q.from_user, f"Viewed: {p['name']}")
    await _render_photo(q, p["image_url"], product_caption(cat, p, idx),
                        product_keyboard(cat_id, idx, len(products)))

# ─────────────────────────────────────────────────────────────────────────────
# ── ORDER CONVERSATION FLOW ───────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Entry: customer taps "🛒 Order Now" on any product (callback_data "ord:{cat}:{idx}")
# Steps: name → phone → address → quantity → notes (optional) → confirm → done
# ─────────────────────────────────────────────────────────────────────────────

_CANCEL_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("❌ Cancel Order", callback_data="ord_cancel"),
]])

_NOTES_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("⏭️ Skip — no special notes", callback_data="ord_skip"),
], [
    InlineKeyboardButton("❌ Cancel Order", callback_data="ord_cancel"),
]])


async def ord_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: customer tapped 'Order Now' on a product."""
    q              = update.callback_query
    _, cat_id, raw = q.data.split(":")
    idx            = int(raw)
    cat            = CAT_MAP.get(cat_id)
    if not cat or idx >= len(cat["products"]):
        await q.answer("Product not found.", show_alert=True)
        return ConversationHandler.END

    product = cat["products"][idx]
    await q.answer()
    await notify_admin(context, q.from_user,
                       f"🛒 Started order: {product['name']} ({_fmt_price(product['price'])})")

    # Store everything needed to build the order later
    context.user_data["order"] = {
        "cat_id":     cat_id,
        "prod_idx":   idx,
        "prod_name":  product["name"],
        "cat_name":   cat["name"],
        "unit_price": product["price"],
    }

    await q.message.reply_text(
        f"🛒 *Order: {product['name']}*\n"
        f"💰 *Price: {_fmt_price(product['price'])}* per item\n\n"
        f"Let's collect your delivery details.\n\n"
        f"*Step 1 of 5* — What is your *full name?*",
        parse_mode="Markdown",
        reply_markup=_CANCEL_KB,
    )
    return ORD_NAME


async def ord_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Guard: order data is lost if the bot restarted mid-conversation.
    if not context.user_data.get("order"):
        await update.message.reply_text(
            "⚠️ Your session expired. Please tap *Order Now* on a product to start again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛍️ Browse Products", callback_data="cats"),
            ]]),
        )
        return ConversationHandler.END

    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text(
            "Please enter your *full name* (at least 2 characters).",
            parse_mode="Markdown", reply_markup=_CANCEL_KB,
        )
        return ORD_NAME

    context.user_data["order"]["full_name"] = name
    # _escape_md prevents a Telegram BadRequest when the name contains
    # Markdown special characters (_ * ` [).  A BadRequest is caught silently
    # by PTB, leaving the user with no reply and the conversation frozen.
    await update.message.reply_text(
        f"✅ Hi, *{_escape_md(name)}*!\n\n"
        f"*Step 2 of 5* — What is your *phone number?*\n"
        f"_(We will use this to contact you about your order.)_",
        parse_mode="Markdown",
        reply_markup=_CANCEL_KB,
    )
    return ORD_PHONE


async def ord_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("order"):
        await update.message.reply_text(
            "⚠️ Your session expired. Please tap *Order Now* on a product to start again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛍️ Browse Products", callback_data="cats"),
            ]]),
        )
        return ConversationHandler.END

    phone = update.message.text.strip()
    # Strip formatting characters then check digit count.
    # Original code used `and` (both conditions must be true to reject), which
    # accidentally accepted any string with 7+ digits regardless of format.
    # Fixed: reject if the cleaned string contains fewer than 7 digits OR is
    # not purely numeric after stripping separators.
    digits = re.sub(r"[\s\-\(\)+]", "", phone)
    if not digits.isdigit() or len(digits) < 7:
        await update.message.reply_text(
            "❌ That doesn't look like a valid phone number. Please try again.\n"
            "_Example: 08012345678 or +2348012345678_",
            parse_mode="Markdown",
            reply_markup=_CANCEL_KB,
        )
        return ORD_PHONE

    context.user_data["order"]["phone"] = phone
    await update.message.reply_text(
        f"*Step 3 of 5* — What is your *delivery address?*\n"
        f"_(Include your street, city, and state.)_",
        parse_mode="Markdown",
        reply_markup=_CANCEL_KB,
    )
    return ORD_ADDRESS


async def ord_get_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("order"):
        await update.message.reply_text(
            "⚠️ Your session expired. Please tap *Order Now* on a product to start again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛍️ Browse Products", callback_data="cats"),
            ]]),
        )
        return ConversationHandler.END

    address = update.message.text.strip()
    if len(address) < 5:
        await update.message.reply_text(
            "Please enter a *complete delivery address*.",
            parse_mode="Markdown", reply_markup=_CANCEL_KB,
        )
        return ORD_ADDRESS

    context.user_data["order"]["address"] = address
    prod_name = _escape_md(context.user_data["order"]["prod_name"])
    await update.message.reply_text(
        f"*Step 4 of 5* — How many units of *{prod_name}* would you like to order?",
        parse_mode="Markdown",
        reply_markup=_CANCEL_KB,
    )
    return ORD_QTY


async def ord_get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("order"):
        await update.message.reply_text(
            "⚠️ Your session expired. Please tap *Order Now* on a product to start again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛍️ Browse Products", callback_data="cats"),
            ]]),
        )
        return ConversationHandler.END

    raw = re.sub(r"[^\d]", "", update.message.text)
    if not raw or int(raw) < 1:
        await update.message.reply_text(
            "❌ Please enter a valid quantity (e.g. 1, 2, 3).",
            reply_markup=_CANCEL_KB,
        )
        return ORD_QTY

    qty        = int(raw)
    unit_price = context.user_data["order"]["unit_price"]
    context.user_data["order"]["quantity"]    = qty
    context.user_data["order"]["total_price"] = qty * unit_price

    await update.message.reply_text(
        f"*Step 5 of 5* — Any *special notes* for your order?\n\n"
        f"_(e.g. preferred colour, size, delivery time, gift wrapping)_\n\n"
        f"Or tap *Skip* if you have no special requests.",
        parse_mode="Markdown",
        reply_markup=_NOTES_KB,
    )
    return ORD_NOTES


async def ord_get_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Customer typed their notes — proceed to confirmation."""
    if not context.user_data.get("order"):
        await update.message.reply_text(
            "⚠️ Your session expired. Please tap *Order Now* on a product to start again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛍️ Browse Products", callback_data="cats"),
            ]]),
        )
        return ConversationHandler.END
    context.user_data["order"]["notes"] = update.message.text.strip()
    return await _show_order_summary(update.message.reply_text, context)


async def ord_skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Customer tapped Skip — proceed to confirmation with no notes."""
    q = update.callback_query; await q.answer()
    context.user_data["order"]["notes"] = ""
    return await _show_order_summary(q.message.reply_text, context)


async def ord_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Customer tapped Cancel during the order flow."""
    q = update.callback_query; await q.answer()
    context.user_data.pop("order", None)
    await q.message.reply_text(
        "❌ Order cancelled. Tap *Order Now* on any product to start again.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
        ]),
    )
    return ConversationHandler.END


async def ord_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Customer typed /cancel during the order flow."""
    context.user_data.pop("order", None)
    await update.message.reply_text(
        "❌ Order cancelled. Tap *Order Now* on any product to start again.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
        ]),
    )
    return ConversationHandler.END


async def _show_order_summary(reply_fn, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Build and send the order confirmation card. Returns ORD_CONFIRM state."""
    o          = context.user_data["order"]
    # Escape all user-supplied strings so Markdown special characters in
    # names, addresses, or notes don't cause a Telegram BadRequest.
    notes_line = f"\n💬 *Notes:* {_escape_md(o['notes'])}" if o.get("notes") else ""
    summary    = (
        f"📋 *Order Summary*\n"
        f"{'─' * 26}\n\n"
        f"🛍️ *Product:* {_escape_md(o['prod_name'])}\n"
        f"📂 *Category:* {_escape_md(o['cat_name'])}\n"
        f"🔢 *Quantity:* {o['quantity']}\n"
        f"💰 *Unit Price:* {_fmt_price(o['unit_price'])}\n"
        f"💵 *Total:* {_fmt_price(o['total_price'])}\n"
        f"{'─' * 26}\n\n"
        f"👤 *Name:* {_escape_md(o['full_name'])}\n"
        f"📞 *Phone:* {_escape_md(o['phone'])}\n"
        f"🏠 *Address:* {_escape_md(o['address'])}"
        f"{notes_line}\n\n"
        f"_Please confirm your order below._"
    )
    confirm_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Order", callback_data="ord_confirm")],
        [InlineKeyboardButton("❌ Cancel",        callback_data="ord_cancel")],
    ])
    await reply_fn(summary, parse_mode="Markdown", reply_markup=confirm_kb)
    return ORD_CONFIRM


async def ord_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Customer confirmed — save order, alert admin, send receipt."""
    q   = update.callback_query; await q.answer("✅ Order placed!")
    o   = context.user_data.get("order", {})
    user = q.from_user

    # Build the full order record
    order = {
        "order_id":    _new_order_id(),
        "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "status":      "pending",
        "product": {
            "name":       o["prod_name"],
            "category":   o["cat_name"],
            "unit_price": o["unit_price"],
        },
        "quantity":    o["quantity"],
        "total_price": o["total_price"],
        "notes":       o.get("notes", ""),
        "customer": {
            "telegram_id":       user.id,
            "telegram_username": f"@{user.username}" if user.username else "—",
            "full_name":         o["full_name"],
            "phone":             o["phone"],
            "address":           o["address"],
        },
    }

    order_id = save_order(order)
    await send_order_to_admin(context, order)

    # Send receipt to customer
    receipt = (
        f"✅ *Order Confirmed!*\n\n"
        f"Thank you, *{o['full_name']}*! Your order has been received.\n\n"
        f"🛍️ *Product:* {o['prod_name']}\n"
        f"🔢 *Quantity:* {o['quantity']}\n"
        f"💵 *Total:* {_fmt_price(o['total_price'])}\n"
        f"📋 *Order ID:* `{order_id}`\n\n"
        f"We will contact you soon on *{o['phone']}* to confirm delivery details.\n\n"
        f"_{STORE['name']} — Thank you for shopping with us! 🙏_"
    )
    await q.message.reply_text(
        receipt,
        parse_mode="Markdown",
        reply_markup=after_order_keyboard(o.get("cat_id", ""), o.get("prod_idx", 0)),
    )

    logger.info("Order %s confirmed for %s", order_id, o["full_name"])
    context.user_data.pop("order", None)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────────
# ── GEMINI AI FALLBACK ────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    user      = update.effective_user
    logger.info("AI fallback — %s: %.60s", user.first_name if user else "?", user_text)
    await notify_admin(context, user, user_text)
    await update.message.chat.send_action("typing")
    try:
        # Hard 25-second timeout so a slow/hung Gemini call never blocks
        # other updates.  Without this, one hanging AI request stalls the
        # entire bot when concurrent_updates=False (PTB default).
        response = await asyncio.wait_for(
            gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_text,
                config=genai_types.GenerateContentConfig(
                    system_instruction=build_system_prompt(),
                    max_output_tokens=1024,
                ),
            ),
            timeout=25.0,
        )
        ai_reply = (response.text or "").strip() or (
            "I'm sorry, I couldn't generate a response. Please try rephrasing."
        )
    except asyncio.TimeoutError:
        logger.error("Gemini request timed out after 25 s")
        ai_reply = (
            "⚠️ The AI assistant is taking too long. "
            "Please try again in a moment."
        )
    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        ai_reply = (
            f"⚠️ I couldn't reach the AI right now. "
            f"Contact us at {STORE.get('contact_email', 'our support team')}."
        )
    await update.message.reply_text(
        ai_reply,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
            [InlineKeyboardButton("📞 Contact Us",       callback_data="contact")],
        ]),
    )

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — keyboards ───────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _adm_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Manage Products", callback_data="adm:products")],
        [InlineKeyboardButton("🏪 Store Settings",   callback_data="adm:settings")],
        [InlineKeyboardButton("❌ Close Panel",      callback_data="adm:close")],
    ])


def _adm_products_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Product",    callback_data="adm:add")],
        [InlineKeyboardButton("✏️ Edit Product",   callback_data="adm:edit")],
        [InlineKeyboardButton("🗑️ Delete Product", callback_data="adm:del")],
        [InlineKeyboardButton("🔙 Back",           callback_data="adm:main")],
    ])


def _adm_cats_kb(action: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{c['emoji']} {c['name']}",
                              callback_data=f"adm_cat:{action}:{c['id']}")]
        for c in CATS
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="adm:products")])
    return InlineKeyboardMarkup(rows)


def _adm_prods_kb(cat_id: str, action: str) -> InlineKeyboardMarkup:
    cat   = CAT_MAP.get(cat_id, {})
    prods = cat.get("products", [])
    rows  = [
        [InlineKeyboardButton(
            f"{p['name']} — {_fmt_price(p['price'])}",
            callback_data=f"adm_prod:{action}:{cat_id}:{i}",
        )]
        for i, p in enumerate(prods)
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"adm:{action}")])
    return InlineKeyboardMarkup(rows)


def _adm_fields_kb(cat_id: str, idx: int) -> InlineKeyboardMarkup:
    base = f"adm_field:{cat_id}:{idx}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Name",        callback_data=f"{base}:name"),
            InlineKeyboardButton("📄 Description", callback_data=f"{base}:desc"),
        ],
        [
            InlineKeyboardButton("💰 Price",       callback_data=f"{base}:price"),
            InlineKeyboardButton("🖼️ Photo",       callback_data=f"{base}:photo"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data=f"adm_cat:edit:{cat_id}")],
    ])


def _adm_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Store Name", callback_data="adm_setting:name")],
        [InlineKeyboardButton("📧 Gmail",      callback_data="adm_setting:email")],
        [InlineKeyboardButton("💬 WhatsApp",   callback_data="adm_setting:whatsapp")],
        [InlineKeyboardButton("🔙 Back",       callback_data="adm:main")],
    ])


def _adm_confirm_kb(yes_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"adm_confirm:yes:{yes_data}"),
        InlineKeyboardButton("❌ Cancel",      callback_data=f"adm_confirm:no"),
    ]])

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — shared helpers ──────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _adm_header() -> str:
    return f"🔐 *{STORE['name']} — Admin Panel*\n\n"


async def _adm_reply(update_or_q, text: str, keyboard: InlineKeyboardMarkup,
                     is_callback: bool = True) -> None:
    if is_callback:
        await update_or_q.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )
    else:
        await update_or_q.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — conversation entry & main menu ──────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        await update.message.reply_text("⛔ Access denied.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        _adm_header() + "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=_adm_main_kb(),
    )
    return ADMIN_MAIN


async def adm_show_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    context.user_data.clear()
    await _adm_reply(q, _adm_header() + "What would you like to do?", _adm_main_kb())
    return ADMIN_MAIN


async def adm_show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await _adm_reply(q, _adm_header() + "📦 *Manage Products*\n\nChoose an action:",
                     _adm_products_kb())
    return ADMIN_MAIN


async def adm_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "✅ Admin panel closed. Type /admin to reopen or /start for the store.",
        reply_markup=None,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Admin panel closed. Type /admin to reopen.")
    context.user_data.clear()
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — ADD product flow ────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def adm_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["new_prod"] = {}
    await _adm_reply(q, _adm_header() + "➕ *Add Product*\n\nSelect the category:",
                     _adm_cats_kb("add"))
    return ADD_CAT


async def add_select_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q      = update.callback_query; await q.answer()
    cat_id = q.data.split(":")[-1]
    context.user_data["new_prod"]["cat_id"] = cat_id
    await _adm_reply(q, _adm_header() + "➕ *Add Product*\n\nSend me the *product name*:",
                     InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm:add")]]))
    return ADD_NAME


async def add_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_prod"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        _adm_header() + "➕ *Add Product*\n\nNow send the *product description*:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm:main")]]),
    )
    return ADD_DESC


async def add_get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_prod"]["description"] = update.message.text.strip()
    await update.message.reply_text(
        _adm_header() + "➕ *Add Product*\n\nNow send the *price in Naira* (numbers only, e.g. `25000`):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm:main")]]),
    )
    return ADD_PRICE


async def add_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = re.sub(r"[^\d]", "", update.message.text)
    if not raw:
        await update.message.reply_text("❌ Please send numbers only (e.g. `25000`).",
                                        parse_mode="Markdown")
        return ADD_PRICE
    context.user_data["new_prod"]["price"] = int(raw)
    await update.message.reply_text(
        _adm_header() + "➕ *Add Product*\n\nFinally, *upload a photo* for this product:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm:main")]]),
    )
    return ADD_PHOTO


async def add_get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_id = update.message.photo[-1].file_id
    np      = context.user_data.get("new_prod", {})
    cat_id  = np.get("cat_id")
    cat     = CAT_MAP.get(cat_id)
    if not cat:
        await update.message.reply_text("❌ Category not found. Start over with /admin.")
        return ConversationHandler.END
    new_product = {
        "id":          _new_prod_id(cat_id),
        "name":        np["name"],
        "description": np["description"],
        "price":       np["price"],
        "image_url":   file_id,
    }
    cat["products"].append(new_product)
    save_catalog()
    await update.message.reply_text(
        f"✅ *{new_product['name']}* added to *{cat['name']}*!\n\n"
        f"Price: {_fmt_price(new_product['price'])}\n"
        f"Customers can see it immediately.",
        parse_mode="Markdown",
        reply_markup=_adm_main_kb(),
    )
    context.user_data.clear()
    return ADMIN_MAIN


async def add_photo_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📸 Please *upload a photo* (not a URL).",
                                    parse_mode="Markdown")
    return ADD_PHOTO

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — EDIT product flow ───────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def adm_start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await _adm_reply(q, _adm_header() + "✏️ *Edit Product*\n\nSelect the category:",
                     _adm_cats_kb("edit"))
    return EDIT_CAT


async def edit_select_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q      = update.callback_query; await q.answer()
    cat_id = q.data.split(":")[-1]
    context.user_data["edit_cat"] = cat_id
    cat = CAT_MAP.get(cat_id)
    if not cat or not cat["products"]:
        await q.answer("No products in this category.", show_alert=True)
        return EDIT_CAT
    await _adm_reply(q,
        _adm_header() + f"✏️ *Edit Product*\n\nCategory: {cat['emoji']} *{cat['name']}*\n\nSelect a product:",
        _adm_prods_kb(cat_id, "edit"))
    return EDIT_PROD


async def edit_select_prod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q                 = update.callback_query; await q.answer()
    _, _, cat_id, raw = q.data.split(":", 3)
    idx               = int(raw)
    context.user_data["edit_prod_idx"] = idx
    context.user_data["edit_cat"]      = cat_id
    cat  = CAT_MAP.get(cat_id, {})
    prod = cat.get("products", [])[idx]
    await _adm_reply(q,
        _adm_header() +
        f"✏️ *Edit Product*\n\n*{prod['name']}*\nPrice: {_fmt_price(prod['price'])}\n\n"
        f"Which field would you like to change?",
        _adm_fields_kb(cat_id, idx))
    return EDIT_FIELD


async def edit_select_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q      = update.callback_query; await q.answer()
    parts  = q.data.split(":")
    field  = parts[-1]
    cat_id = parts[1]
    idx    = int(parts[2])
    context.user_data["edit_field"]    = field
    context.user_data["edit_cat"]      = cat_id
    context.user_data["edit_prod_idx"] = idx
    if field == "photo":
        await _adm_reply(q,
            _adm_header() + "✏️ *Edit Product*\n\nUpload the new product photo:",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"adm_prod:edit:{cat_id}:{idx}")]]))
        return EDIT_PHOTO
    labels = {"name": "product name", "desc": "description", "price": "price (numbers only)"}
    await _adm_reply(q,
        _adm_header() + f"✏️ *Edit Product*\n\nSend the new *{labels.get(field, field)}*:",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"adm_prod:edit:{cat_id}:{idx}")]]))
    return EDIT_VAL


async def edit_get_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field  = context.user_data.get("edit_field")
    cat_id = context.user_data.get("edit_cat")
    idx    = context.user_data.get("edit_prod_idx", 0)
    cat    = CAT_MAP.get(cat_id)
    if not cat:
        await update.message.reply_text("❌ Session expired. Run /admin again.")
        return ConversationHandler.END
    prod = cat["products"][idx]
    text = update.message.text.strip()
    if field == "price":
        raw = re.sub(r"[^\d]", "", text)
        if not raw:
            await update.message.reply_text("❌ Numbers only (e.g. `25000`).", parse_mode="Markdown")
            return EDIT_VAL
        prod["price"] = int(raw)
    elif field == "name":
        prod["name"] = text
    elif field == "desc":
        prod["description"] = text
    save_catalog()
    await update.message.reply_text(
        f"✅ *{prod['name']}* updated! Customers see the change immediately.",
        parse_mode="Markdown",
        reply_markup=_adm_main_kb(),
    )
    context.user_data.clear()
    return ADMIN_MAIN


async def edit_get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cat_id = context.user_data.get("edit_cat")
    idx    = context.user_data.get("edit_prod_idx", 0)
    cat    = CAT_MAP.get(cat_id)
    if not cat:
        await update.message.reply_text("❌ Session expired. Run /admin again.")
        return ConversationHandler.END
    prod              = cat["products"][idx]
    prod["image_url"] = update.message.photo[-1].file_id
    save_catalog()
    await update.message.reply_text(
        f"✅ Photo updated for *{prod['name']}*! Customers see the change immediately.",
        parse_mode="Markdown",
        reply_markup=_adm_main_kb(),
    )
    context.user_data.clear()
    return ADMIN_MAIN


async def edit_photo_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📸 Please *upload a photo* (not a URL).",
                                    parse_mode="Markdown")
    return EDIT_PHOTO

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — DELETE product flow ─────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def adm_start_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await _adm_reply(q, _adm_header() + "🗑️ *Delete Product*\n\nSelect the category:",
                     _adm_cats_kb("del"))
    return DEL_CAT


async def del_select_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q      = update.callback_query; await q.answer()
    cat_id = q.data.split(":")[-1]
    context.user_data["del_cat"] = cat_id
    cat = CAT_MAP.get(cat_id)
    if not cat or not cat["products"]:
        await q.answer("No products in this category.", show_alert=True)
        return DEL_CAT
    await _adm_reply(q,
        _adm_header() + f"🗑️ *Delete Product*\n\nCategory: {cat['emoji']} *{cat['name']}*\n\nSelect a product:",
        _adm_prods_kb(cat_id, "del"))
    return DEL_PROD


async def del_select_prod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q                 = update.callback_query; await q.answer()
    _, _, cat_id, raw = q.data.split(":", 3)
    idx               = int(raw)
    cat               = CAT_MAP.get(cat_id)
    prod              = cat["products"][idx]
    context.user_data["del_cat"]      = cat_id
    context.user_data["del_prod_idx"] = idx
    await _adm_reply(q,
        _adm_header() +
        f"🗑️ *Delete Product*\n\n"
        f"Are you sure you want to delete:\n\n"
        f"*{prod['name']}*\nPrice: {_fmt_price(prod['price'])}\n\n"
        f"⚠️ This cannot be undone.",
        _adm_confirm_kb(f"{cat_id}:{idx}"))
    return DEL_CONFIRM


async def del_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q        = update.callback_query; await q.answer()
    parts    = q.data.split(":")
    decision = parts[1]
    if decision == "no":
        await _adm_reply(q, _adm_header() + "Deletion cancelled.", _adm_main_kb())
        context.user_data.clear()
        return ADMIN_MAIN
    cat_id  = parts[2]
    idx     = int(parts[3])
    cat     = CAT_MAP.get(cat_id)
    if not cat or idx >= len(cat["products"]):
        await _adm_reply(q, "❌ Product not found.", _adm_main_kb())
        return ADMIN_MAIN
    removed = cat["products"].pop(idx)
    save_catalog()
    await _adm_reply(q,
        _adm_header() + f"✅ *{removed['name']}* deleted. Catalog updated immediately.",
        _adm_main_kb())
    context.user_data.clear()
    return ADMIN_MAIN

# ─────────────────────────────────────────────────────────────────────────────
# ── ADMIN PANEL — STORE SETTINGS flow ─────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def adm_show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await _adm_reply(q,
        _adm_header() +
        f"🏪 *Store Settings*\n\n"
        f"Current values:\n"
        f"• Name: *{STORE['name']}*\n"
        f"• Email: {STORE.get('contact_email', '—')}\n"
        f"• WhatsApp: {STORE.get('contact_whatsapp', '—')}\n\n"
        f"Select a setting to change:",
        _adm_settings_kb())
    return SETTINGS_FIELD


async def settings_select_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q     = update.callback_query; await q.answer()
    field = q.data.split(":")[-1]
    context.user_data["settings_field"] = field
    labels = {
        "name":     ("Store Name",    STORE.get("name", "")),
        "email":    ("Gmail Address", STORE.get("contact_email", "")),
        "whatsapp": ("WhatsApp",      STORE.get("contact_whatsapp", "")),
    }
    label, current = labels.get(field, (field, ""))
    await _adm_reply(q,
        _adm_header() + f"🏪 *Store Settings*\n\nCurrent *{label}*: `{current}`\n\nSend the new value:",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm:settings")]]))
    return SETTINGS_VAL


async def settings_get_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field    = context.user_data.get("settings_field")
    value    = update.message.text.strip()
    if not value:
        await update.message.reply_text("❌ Value cannot be empty."); return SETTINGS_VAL
    field_map = {"name": "name", "email": "contact_email", "whatsapp": "contact_whatsapp"}
    json_key  = field_map.get(field)
    if not json_key:
        await update.message.reply_text("❌ Unknown field."); return ConversationHandler.END
    STORE[json_key] = value
    save_catalog()
    await update.message.reply_text(
        f"✅ *{json_key.replace('_', ' ').title()}* updated to:\n`{value}`\n\nChange is live immediately.",
        parse_mode="Markdown",
        reply_markup=_adm_main_kb(),
    )
    context.user_data.clear()
    return ADMIN_MAIN

# ─────────────────────────────────────────────────────────────────────────────
# ── APPLICATION ERROR HANDLER ─────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def global_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Catch-all for unhandled exceptions inside any handler.

    Without this, python-telegram-bot silently logs the error and sends
    nothing to the user, leaving the conversation frozen with no feedback.
    This handler logs the full traceback and always sends the user a short
    recovery message so they know something went wrong.
    """
    logger.error(
        "Unhandled exception for update %s", update, exc_info=context.error
    )
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Something went wrong on our end. "
                "Please type /cancel and try again, or tap a menu button.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🛍️ Browse Products", callback_data="cats"),
                    InlineKeyboardButton("🏠 Home", callback_data="home"),
                ]]),
            )
        except Exception:
            pass  # best-effort — don't raise a second error


# ─────────────────────────────────────────────────────────────────────────────
# ── MAIN ─────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def build_app() -> "Application":
    """Build and return the configured Application without starting polling.

    Separated from main() so that main.py can manage the event loop itself,
    run the health-check server alongside the bot in a single asyncio loop,
    and perform explicit webhook deletion before polling begins.
    """
    for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY"):
        if not os.environ.get(key):
            raise RuntimeError(f"{key} is not set. Add it as an environment variable.")

    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_BOT_TOKEN"])
        # Process each user's updates concurrently so a slow handler (e.g.
        # Gemini AI call, photo upload) never blocks other users' messages.
        # ConversationHandlers remain safe: PTB serialises updates per user
        # even with concurrent_updates=True.
        .concurrent_updates(True)
        .build()
    )

    # ── Order ConversationHandler (registered first — highest priority) ───────
    order_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ord_entry, pattern=r"^ord:[^:]+:\d+$"),
        ],
        states={
            ORD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_name),
                CallbackQueryHandler(ord_cancel_cb, pattern=r"^ord_cancel$"),
            ],
            ORD_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_phone),
                CallbackQueryHandler(ord_cancel_cb, pattern=r"^ord_cancel$"),
            ],
            ORD_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_address),
                CallbackQueryHandler(ord_cancel_cb, pattern=r"^ord_cancel$"),
            ],
            ORD_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_qty),
                CallbackQueryHandler(ord_cancel_cb, pattern=r"^ord_cancel$"),
            ],
            ORD_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_notes),
                CallbackQueryHandler(ord_skip_notes, pattern=r"^ord_skip$"),
                CallbackQueryHandler(ord_cancel_cb,  pattern=r"^ord_cancel$"),
            ],
            ORD_CONFIRM: [
                CallbackQueryHandler(ord_confirm,   pattern=r"^ord_confirm$"),
                CallbackQueryHandler(ord_cancel_cb, pattern=r"^ord_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", ord_cancel_cmd)],
        allow_reentry=True,
        per_message=False,
        name="order_flow",
    )
    app.add_handler(order_conv)

    # ── Admin ConversationHandler ─────────────────────────────────────────────
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_start)],
        states={
            ADMIN_MAIN: [
                CallbackQueryHandler(adm_show_main,     pattern=r"^adm:main$"),
                CallbackQueryHandler(adm_show_products, pattern=r"^adm:products$"),
                CallbackQueryHandler(adm_start_add,     pattern=r"^adm:add$"),
                CallbackQueryHandler(adm_start_edit,    pattern=r"^adm:edit$"),
                CallbackQueryHandler(adm_start_del,     pattern=r"^adm:del$"),
                CallbackQueryHandler(adm_show_settings, pattern=r"^adm:settings$"),
                CallbackQueryHandler(adm_close,         pattern=r"^adm:close$"),
            ],
            ADD_CAT: [
                CallbackQueryHandler(add_select_cat,    pattern=r"^adm_cat:add:"),
                CallbackQueryHandler(adm_show_products, pattern=r"^adm:products$"),
            ],
            ADD_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_name)],
            ADD_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_desc)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_price)],
            ADD_PHOTO: [
                MessageHandler(filters.PHOTO,                   add_get_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_photo_prompt),
            ],
            EDIT_CAT: [
                CallbackQueryHandler(edit_select_cat,   pattern=r"^adm_cat:edit:"),
                CallbackQueryHandler(adm_show_products, pattern=r"^adm:products$"),
            ],
            EDIT_PROD: [
                CallbackQueryHandler(edit_select_prod,  pattern=r"^adm_prod:edit:"),
                CallbackQueryHandler(adm_start_edit,    pattern=r"^adm:edit$"),
            ],
            EDIT_FIELD: [
                CallbackQueryHandler(edit_select_field, pattern=r"^adm_field:"),
                CallbackQueryHandler(edit_select_cat,   pattern=r"^adm_cat:edit:"),
            ],
            EDIT_VAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_get_value),
                CallbackQueryHandler(edit_select_prod,  pattern=r"^adm_prod:edit:"),
            ],
            EDIT_PHOTO: [
                MessageHandler(filters.PHOTO,                   edit_get_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_photo_prompt),
                CallbackQueryHandler(edit_select_prod,  pattern=r"^adm_prod:edit:"),
            ],
            DEL_CAT: [
                CallbackQueryHandler(del_select_cat,    pattern=r"^adm_cat:del:"),
                CallbackQueryHandler(adm_show_products, pattern=r"^adm:products$"),
            ],
            DEL_PROD: [
                CallbackQueryHandler(del_select_prod,   pattern=r"^adm_prod:del:"),
                CallbackQueryHandler(adm_start_del,     pattern=r"^adm:del$"),
            ],
            DEL_CONFIRM: [
                CallbackQueryHandler(del_confirm, pattern=r"^adm_confirm:"),
            ],
            SETTINGS_FIELD: [
                CallbackQueryHandler(settings_select_field, pattern=r"^adm_setting:"),
                CallbackQueryHandler(adm_show_main,         pattern=r"^adm:main$"),
            ],
            SETTINGS_VAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_get_value),
                CallbackQueryHandler(adm_show_settings,     pattern=r"^adm:settings$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_cancel),
            CommandHandler("admin",  admin_start),
        ],
        allow_reentry=True,
        per_message=False,
        name="admin_panel",
    )
    app.add_handler(admin_conv)

    # ── Application-level error handler ───────────────────────────────────────
    # Must be registered AFTER all ConversationHandlers so it catches exceptions
    # from any of them.  Without this, PTB silently swallows errors and the user
    # gets no reply, which is the root cause of the frozen conversation bug.
    app.add_error_handler(global_error_handler)

    # ── Customer commands ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CommandHandler("myid",  myid_command))

    # ── Customer inline-button callbacks ──────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_home,     pattern=r"^home$"))
    app.add_handler(CallbackQueryHandler(cb_cats,     pattern=r"^cats$"))
    app.add_handler(CallbackQueryHandler(cb_contact,  pattern=r"^contact$"))
    app.add_handler(CallbackQueryHandler(cb_category, pattern=r"^cat:.+$"))
    app.add_handler(CallbackQueryHandler(cb_navigate, pattern=r"^nav:.+:\d+$"))

    # ── Gemini AI fallback ────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    return app


def main() -> None:
    """Entry point for local development (python telegram-bot/bot.py).

    On Render, main.py calls build_app() directly so it can manage the event
    loop, run the health-check server alongside the bot, and delete any
    stale webhook before polling starts.
    """
    app = build_app()
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
