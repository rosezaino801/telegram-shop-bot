"""
StyleVault Shopping Bot
========================
A professional Telegram shopping bot powered by python-telegram-bot v20+
and Google Gemini AI.

Product catalog is loaded from products.json in the same directory.
To add, remove, or update products/prices, edit products.json only —
no changes to this file are needed.

Navigation flow:
  /start       → Store banner + main menu (photo message)
  Browse       → Category list            (edit same message)
  Category     → Product carousel         (edit same message)
  ◀ / ▶       → Navigate products        (edit same message)
  Buy Now      → Order instructions       (new reply message)
  Contact Us   → Contact details          (edit same message)
  🏠 Home      → Back to banner           (edit same message)

All navigation edits the single catalog photo message in place,
keeping the chat clean and professional.
"""

import json
import logging
import os
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
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load product catalog from products.json
# ---------------------------------------------------------------------------
PRODUCTS_FILE = Path(__file__).parent / "products.json"


def load_catalog() -> dict:
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


catalog = load_catalog()
STORE   = catalog["store"]
CATS    = catalog["categories"]            # ordered list of category dicts
CAT_MAP = {c["id"]: c for c in CATS}      # id → category dict

logger.info(
    "Catalog loaded: %d categories, %d products total",
    len(CATS),
    sum(len(c["products"]) for c in CATS),
)

# ---------------------------------------------------------------------------
# Gemini AI client  (answers free-text customer questions)
# ---------------------------------------------------------------------------
gemini_client = genai.Client()   # reads GEMINI_API_KEY from env
GEMINI_MODEL  = "gemini-3.1-flash-lite"

SYSTEM_PROMPT = (
    f"You are a friendly and knowledgeable shopping assistant for {STORE['name']}, "
    f"a premium Nigerian fashion and lifestyle store. "
    "Help customers with product questions, sizing, availability, and delivery. "
    "Be concise and warm. "
    f"If a customer wants to place an order or needs further help, direct them to "
    f"{STORE.get('contact_email', 'our support team')} or "
    f"WhatsApp {STORE.get('contact_whatsapp', 'our WhatsApp')}."
)

# ---------------------------------------------------------------------------
# Admin notifications
# ---------------------------------------------------------------------------
_raw_admin_id = os.environ.get("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID: int | None = (
    int(_raw_admin_id) if _raw_admin_id.lstrip("-").isdigit() else None
)

if ADMIN_CHAT_ID:
    logger.info("Admin notifications enabled → chat ID %d", ADMIN_CHAT_ID)
else:
    logger.warning("ADMIN_CHAT_ID not set — admin notifications are disabled.")


async def notify_admin(
    context: ContextTypes.DEFAULT_TYPE, user, action: str
) -> None:
    """Forward a user interaction summary to the admin chat."""
    if not ADMIN_CHAT_ID:
        return
    if user and user.id == ADMIN_CHAT_ID:
        return   # don't notify the admin about their own actions

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Unknown"
    username  = f"@{user.username}" if user.username else "_(no username)_"

    text = (
        f"🔔 *New interaction*\n\n"
        f"👤 *Name:* {full_name}\n"
        f"🆔 *User ID:* `{user.id}`\n"
        f"📎 *Username:* {username}\n"
        f"💬 *Action:* {action}"
    )
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown"
        )
    except Exception as exc:
        logger.error("Admin notification failed: %s", exc)

# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
        [InlineKeyboardButton("📞 Contact Us",       callback_data="contact")],
    ])


def cats_keyboard() -> InlineKeyboardMarkup:
    """Two category buttons per row, plus a Home button at the bottom."""
    rows: list[list[InlineKeyboardButton]] = []
    row:  list[InlineKeyboardButton]       = []
    for cat in CATS:
        row.append(
            InlineKeyboardButton(
                f"{cat['emoji']} {cat['name']}",
                callback_data=f"cat:{cat['id']}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def product_keyboard(cat_id: str, idx: int, total: int) -> InlineKeyboardMarkup:
    """Carousel navigation + Buy Now + back controls."""
    prev_idx = (idx - 1) % total
    next_idx = (idx + 1) % total
    rows: list[list[InlineKeyboardButton]] = []
    if total > 1:
        rows.append([
            InlineKeyboardButton("◀ Prev", callback_data=f"nav:{cat_id}:{prev_idx}"),
            InlineKeyboardButton("Next ▶", callback_data=f"nav:{cat_id}:{next_idx}"),
        ])
    rows.append([InlineKeyboardButton("🛒 Buy Now", callback_data=f"buy:{cat_id}:{idx}")])
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


def after_buy_keyboard(cat_id: str, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Product", callback_data=f"nav:{cat_id}:{idx}")],
        [InlineKeyboardButton("🛍️ Browse More",    callback_data="cats")],
        [InlineKeyboardButton("🏠 Home",            callback_data="home")],
    ])

# ---------------------------------------------------------------------------
# Caption builders
# ---------------------------------------------------------------------------

def _fmt_price(price: int) -> str:
    return f"₦{price:,}"


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
    if email:
        lines.append(f"📧 *Email:* {email}")
    if whatsapp:
        lines.append(f"💬 *WhatsApp:* {whatsapp}")
    lines += [
        f"\n🕐 *Hours:* {hours}",
        "\nWe typically respond within *2 business hours*.",
        "Please include the product name and your delivery address for faster service.",
    ]
    return "\n".join(lines)


def order_caption(product: dict, cat_id: str, idx: int) -> str:
    email    = STORE.get("contact_email", "")
    whatsapp = STORE.get("contact_whatsapp", "")
    instructions = STORE.get("order_instructions", "Contact us to place your order.")
    text = (
        f"🛒 *Place Your Order*\n\n"
        f"*Product:* {product['name']}\n"
        f"*Price:* {_fmt_price(product['price'])}\n\n"
        f"{instructions}\n\n"
    )
    if email:
        text += f"📧 *Email:* {email}\n"
    if whatsapp:
        text += f"💬 *WhatsApp:* {whatsapp}\n"
    return text

# ---------------------------------------------------------------------------
# Core render helper — works whether the existing message is a photo or text
# ---------------------------------------------------------------------------

async def _render_photo(
    q,
    photo_url: str,
    caption: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    """
    Edit the current message to display a photo.
    If the message already contains a photo, edit it in-place (clean UX).
    If it is a text message (e.g. after a Buy Now reply), delete it and
    send a fresh photo so the catalog continues from the correct message.
    """
    media = InputMediaPhoto(
        media=photo_url,
        caption=caption,
        parse_mode="Markdown",
    )
    if q.message.photo:
        await q.edit_message_media(media=media, reply_markup=keyboard)
    else:
        # The button was on a plain-text message — replace cleanly
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.chat.send_photo(
            photo=photo_url,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — sends the store banner with the main menu."""
    user = update.effective_user
    logger.info("User %s (%s) sent /start", user.first_name if user else "?", user.id if user else "?")
    await notify_admin(context, user, "/start — opened the store")
    await update.message.reply_photo(
        photo=STORE["banner_image"],
        caption=home_caption(),
        parse_mode="Markdown",
        reply_markup=home_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — brief usage guide."""
    user = update.effective_user
    await notify_admin(context, user, "/help")
    await update.message.reply_text(
        f"ℹ️ *{STORE['name']} — Help*\n\n"
        "Commands:\n"
        "/start — Open the store\n"
        "/help  — Show this message\n"
        "/myid  — Show your Telegram user ID\n\n"
        "Use the buttons in the store to browse products and place orders. "
        "Or just *type a question* and our AI assistant will answer it 🤖",
        parse_mode="Markdown",
        reply_markup=home_keyboard(),
    )


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myid — replies with the sender's numeric chat ID (useful for ADMIN_CHAT_ID)."""
    await update.message.reply_text(
        f"🆔 Your numeric Telegram ID is:\n\n`{update.effective_chat.id}`\n\n"
        "Copy this and save it as `ADMIN_CHAT_ID` in Replit Secrets to receive "
        "admin notifications.",
        parse_mode="Markdown",
    )

# ---------------------------------------------------------------------------
# Inline-button callback handlers
# ---------------------------------------------------------------------------

async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await notify_admin(context, q.from_user, "🏠 Home")
    await _render_photo(q, STORE["banner_image"], home_caption(), home_keyboard())


async def cb_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await notify_admin(context, q.from_user, "📂 Browse categories")
    await _render_photo(q, STORE["banner_image"], cats_caption(), cats_keyboard())


async def cb_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await notify_admin(context, q.from_user, "📞 Contact Us")
    await _render_photo(q, STORE["banner_image"], contact_caption(), contact_keyboard())


async def cb_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped a category — show the first product in that category."""
    q      = update.callback_query
    cat_id = q.data.split(":", 1)[1]
    cat    = CAT_MAP.get(cat_id)

    if not cat or not cat["products"]:
        await q.answer("No products in this category yet.", show_alert=True)
        return

    await q.answer()
    product = cat["products"][0]
    logger.info("Category: %s (user %s)", cat["name"], q.from_user.first_name)
    await notify_admin(context, q.from_user, f"Browsed: {cat['emoji']} {cat['name']}")
    await _render_photo(
        q,
        product["image_url"],
        product_caption(cat, product, 0),
        product_keyboard(cat_id, 0, len(cat["products"])),
    )


async def cb_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped ◀ Prev or Next ▶ — navigate the product carousel."""
    q              = update.callback_query
    _, cat_id, raw = q.data.split(":")
    idx            = int(raw)
    cat            = CAT_MAP.get(cat_id)

    if not cat:
        await q.answer("Category not found.", show_alert=True)
        return

    products = cat["products"]
    idx      = idx % len(products)   # safety clamp
    product  = products[idx]

    await q.answer()
    logger.info("Navigate: %s #%d (user %s)", cat_id, idx, q.from_user.first_name)
    await notify_admin(context, q.from_user, f"Viewed: {product['name']}")
    await _render_photo(
        q,
        product["image_url"],
        product_caption(cat, product, idx),
        product_keyboard(cat_id, idx, len(products)),
    )


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped Buy Now — send order instructions as a new reply message."""
    q              = update.callback_query
    _, cat_id, raw = q.data.split(":")
    idx            = int(raw)
    cat            = CAT_MAP.get(cat_id)

    if not cat or idx >= len(cat["products"]):
        await q.answer("Product not found.", show_alert=True)
        return

    product = cat["products"][idx]
    await q.answer("📦 Order details sent below!")
    logger.info("Buy Now: %s (user %s)", product["name"], q.from_user.first_name)
    await notify_admin(
        context, q.from_user,
        f"🛒 Buy Now — {product['name']} ({_fmt_price(product['price'])})"
    )
    # Send as a new reply so the product photo stays visible above
    await q.message.reply_text(
        order_caption(product, cat_id, idx),
        parse_mode="Markdown",
        reply_markup=after_buy_keyboard(cat_id, idx),
    )

# ---------------------------------------------------------------------------
# Gemini AI fallback  (free-text messages that aren't commands)
# ---------------------------------------------------------------------------

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route free-text questions to Gemini and reply with the AI's answer."""
    user_text = update.message.text
    user      = update.effective_user

    logger.info(
        "AI fallback — %s: %.60s",
        user.first_name if user else "?",
        user_text,
    )
    await notify_admin(context, user, user_text)
    await update.message.chat.send_action("typing")

    try:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_text,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=1024,
            ),
        )
        ai_reply = (response.text or "").strip()
        if not ai_reply:
            ai_reply = (
                "I'm sorry, I couldn't generate a response for that. "
                "Please try rephrasing, or contact us directly."
            )
        logger.info("Gemini replied (%d chars)", len(ai_reply))

    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        email    = STORE.get("contact_email", "our support team")
        ai_reply = (
            f"⚠️ I couldn't reach the AI right now. "
            f"For immediate help please contact us at {email}."
        )

    await update.message.reply_text(
        ai_reply,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Browse Products", callback_data="cats")],
            [InlineKeyboardButton("📞 Contact Us",       callback_data="contact")],
        ]),
    )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY"):
        if not os.environ.get(key):
            raise RuntimeError(f"{key} is not set. Add it in Replit Secrets.")

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CommandHandler("myid",  myid_command))

    # Inline-button callbacks  (order matters — more specific patterns first)
    app.add_handler(CallbackQueryHandler(cb_home,     pattern=r"^home$"))
    app.add_handler(CallbackQueryHandler(cb_cats,     pattern=r"^cats$"))
    app.add_handler(CallbackQueryHandler(cb_contact,  pattern=r"^contact$"))
    app.add_handler(CallbackQueryHandler(cb_category, pattern=r"^cat:.+$"))
    app.add_handler(CallbackQueryHandler(cb_navigate, pattern=r"^nav:.+:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_buy,      pattern=r"^buy:.+:\d+$"))

    # AI fallback for any free-text message
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
