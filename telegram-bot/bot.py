"""
Telegram Customer Support Bot
================================
Uses python-telegram-bot v20+ and the Google Gemini SDK (google-genai).

Handler routing:
  /start, /help, /myid  → CommandHandler
  Inline buttons         → CallbackQueryHandler  (callback_data strings)
  Button label text      → MessageHandler + Regex (covers old ReplyKeyboard users
                            and anyone who types the label manually)
  Everything else        → Gemini AI fallback

Admin notifications:
  Every incoming interaction (text messages and button presses) is forwarded to
  the admin chat specified by ADMIN_CHAT_ID. Set that secret in Replit Secrets.
  If ADMIN_CHAT_ID is not set the bot still works — notifications are silently
  skipped. Notification failures never crash the bot.
"""

import logging
import os

from google import genai
from google.genai import types as genai_types
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
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
# Gemini client
# ---------------------------------------------------------------------------
gemini_client = genai.Client()      # reads GEMINI_API_KEY from env
GEMINI_MODEL  = "gemini-3.1-flash-lite"

SYSTEM_PROMPT = (
    "You are a friendly and helpful customer support assistant for our business. "
    "Answer questions clearly and concisely. "
    "If you don't know something, say so honestly and suggest the user contact "
    "the support team at support@example.com."
)

# ---------------------------------------------------------------------------
# Admin notification config
# ---------------------------------------------------------------------------
# Set ADMIN_CHAT_ID in Replit Secrets to your numeric Telegram chat ID.
# Use /myid in your bot chat to discover your numeric ID.
_raw_admin_id = os.environ.get("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID: int | None = int(_raw_admin_id) if _raw_admin_id.lstrip("-").isdigit() else None

if ADMIN_CHAT_ID:
    logger.info("Admin notifications enabled → chat ID %d", ADMIN_CHAT_ID)
else:
    logger.warning(
        "ADMIN_CHAT_ID is not set. Admin notifications are disabled. "
        "Send /myid to the bot from your Telegram account to find your numeric ID, "
        "then add it as ADMIN_CHAT_ID in Replit Secrets."
    )

# ---------------------------------------------------------------------------
# Admin notification helper
# ---------------------------------------------------------------------------

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, user, action: str) -> None:
    """
    Forward a user interaction to the admin chat.

    Parameters
    ----------
    context : ContextTypes.DEFAULT_TYPE
        Handler context (provides access to context.bot).
    user    : telegram.User
        The Telegram user who triggered the action.
    action  : str
        Human-readable description of what the user did / sent.
    """
    if not ADMIN_CHAT_ID:
        return  # notifications disabled — ADMIN_CHAT_ID not configured

    # Don't notify about the admin's own interactions
    if user and user.id == ADMIN_CHAT_ID:
        return

    first_name = user.first_name or ""
    last_name  = user.last_name  or ""
    full_name  = f"{first_name} {last_name}".strip() or "Unknown"
    username   = f"@{user.username}" if user.username else "_(no username)_"
    user_id    = user.id

    notification = (
        f"🔔 *New user interaction*\n\n"
        f"👤 *Name:* {full_name}\n"
        f"🆔 *User ID:* `{user_id}`\n"
        f"📎 *Username:* {username}\n"
        f"💬 *Message:* {action}"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=notification,
            parse_mode="Markdown",
        )
    except Exception as exc:
        # Never crash the bot over a notification failure
        logger.error("Failed to send admin notification: %s", exc)

# ---------------------------------------------------------------------------
# Inline keyboard
# ---------------------------------------------------------------------------
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🛍️ Products",        callback_data="products"),
        InlineKeyboardButton("💰 Prices",           callback_data="prices"),
    ],
    [
        InlineKeyboardButton("🎧 Contact Support",  callback_data="contact_support"),
        InlineKeyboardButton("ℹ️ About Us",         callback_data="about_us"),
    ],
])

# ---------------------------------------------------------------------------
# Content helpers — shared by both inline-button and text-pattern handlers
# ---------------------------------------------------------------------------

def _products_text() -> str:
    return (
        "🛍️ *Our Products*\n\n"
        "Here's a quick overview of what we offer:\n\n"
        "• *Product A* — Premium quality, best-seller\n"
        "• *Product B* — Budget-friendly option\n"
        "• *Product C* — Limited edition, hurry!\n\n"
        "Want more details? Tap 🎧 *Contact Support* and our team will help you out."
    )

def _prices_text() -> str:
    return (
        "💰 *Pricing*\n\n"
        "Here are our current prices:\n\n"
        "• *Product A* — $49.99\n"
        "• *Product B* — $19.99\n"
        "• *Product C* — $79.99 *(limited edition)*\n\n"
        "All prices include VAT. Bulk discounts available — contact support for details."
    )

def _contact_text() -> str:
    return (
        "🎧 *Contact Support*\n\n"
        "Our team is ready to help you!\n\n"
        "📧 *Email:* support@example.com\n"
        "🕐 *Hours:* Mon–Fri, 9 AM – 6 PM (UTC)\n\n"
        "Leave your question here and a team member will follow up. "
        "We typically respond within *2 business hours*."
    )

def _about_text() -> str:
    return (
        "ℹ️ *About Us*\n\n"
        "We're a passionate team dedicated to delivering high-quality "
        "products and exceptional customer service.\n\n"
        "🏢 Founded in 2020\n"
        "🌍 Serving customers worldwide\n"
        "⭐ 4.9 / 5 average customer rating\n\n"
        "Follow us online:\n"
        "• 🐦 Twitter: @example\n"
        "• 📘 Facebook: /example\n"
        "• 📸 Instagram: @example"
    )

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — clears any lingering reply keyboard, then shows the inline menu."""
    user       = update.effective_user
    first_name = user.first_name if user else "there"
    username   = f"@{user.username}" if (user and user.username) else first_name

    logger.info("User %s (%s) sent /start", first_name, username)
    await notify_admin(context, user, "/start — opened the bot")

    welcome_text = (
        f"👋 Hello, {first_name}! Welcome to our Customer Support bot.\n\n"
        "I'm here to help you with:\n"
        "• Product information\n"
        "• Pricing details\n"
        "• Getting in touch with our support team\n"
        "• Learning more about us\n\n"
        "Tap a button below, or just type your question and I'll answer it 💬"
    )

    # ReplyKeyboardRemove dismisses any old reply keyboard that may still be
    # showing from a previous version of this bot.  The inline keyboard is
    # attached to the second message so the two don't collide.
    await update.message.reply_text(
        "🔄 Refreshing menu…", reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(welcome_text, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — command reference."""
    user = update.effective_user
    logger.info("User %s sent /help", user.first_name if user else "unknown")
    await notify_admin(context, user, "/help — requested help menu")

    help_text = (
        "🆘 *Help Menu*\n\n"
        "Commands:\n"
        "/start — Show the main menu\n"
        "/help  — Show this message\n\n"
        "*Menu buttons:*\n"
        "🛍️ *Products* — Browse our catalogue\n"
        "💰 *Prices*   — View current pricing\n"
        "🎧 *Contact Support* — Reach a human agent\n"
        "ℹ️ *About Us* — Learn who we are\n\n"
        "Or just *type any question* and our AI will answer it 🤖"
    )
    await update.message.reply_text(
        help_text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
    )


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myid — replies with the sender's numeric Telegram chat ID.
    Useful for finding the value to set as ADMIN_CHAT_ID in Replit Secrets.
    """
    user    = update.effective_user
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🆔 Your numeric Telegram ID is:\n\n`{chat_id}`\n\n"
        "Copy this number and add it as `ADMIN_CHAT_ID` in Replit Secrets "
        "to enable admin notifications.",
        parse_mode="Markdown",
    )
    logger.info("/myid requested by %s — chat ID %d", user.first_name if user else "unknown", chat_id)

# ---------------------------------------------------------------------------
# Inline-button handlers  (callback_data from InlineKeyboardButton)
# ---------------------------------------------------------------------------

async def cb_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    logger.info("Inline button: products (user %s)", q.from_user.first_name)
    await notify_admin(context, q.from_user, "🛍️ Products _(button press)_")
    await q.edit_message_text(_products_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def cb_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    logger.info("Inline button: prices (user %s)", q.from_user.first_name)
    await notify_admin(context, q.from_user, "💰 Prices _(button press)_")
    await q.edit_message_text(_prices_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def cb_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    logger.info("Inline button: contact_support (user %s)", q.from_user.first_name)
    await notify_admin(context, q.from_user, "🎧 Contact Support _(button press)_")
    await q.edit_message_text(_contact_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def cb_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    logger.info("Inline button: about_us (user %s)", q.from_user.first_name)
    await notify_admin(context, q.from_user, "ℹ️ About Us _(button press)_")
    await q.edit_message_text(_about_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

# ---------------------------------------------------------------------------
# Text-pattern handlers  (old reply-keyboard buttons or typed labels)
# bot.hears() equivalent: MessageHandler + filters.Regex
# ---------------------------------------------------------------------------
# These catch the exact label text that the old ReplyKeyboard buttons sent,
# so users who still have that keyboard cached see correct responses instead
# of an AI answer.

async def txt_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("Text pattern: Products (user %s)", user.first_name)
    await notify_admin(context, user, update.message.text)
    await update.message.reply_text(_products_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def txt_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("Text pattern: Prices (user %s)", user.first_name)
    await notify_admin(context, user, update.message.text)
    await update.message.reply_text(_prices_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def txt_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("Text pattern: Contact Support (user %s)", user.first_name)
    await notify_admin(context, user, update.message.text)
    await update.message.reply_text(_contact_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def txt_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("Text pattern: About Us (user %s)", user.first_name)
    await notify_admin(context, user, update.message.text)
    await update.message.reply_text(_about_text(), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

# ---------------------------------------------------------------------------
# Gemini AI fallback  (any text that matched nothing above)
# ---------------------------------------------------------------------------

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the user's free-text message to Gemini and replies with the result."""
    user_text  = update.message.text
    first_name = update.effective_user.first_name if update.effective_user else "unknown"
    username   = update.effective_user.username   if update.effective_user else "unknown"

    logger.info("AI fallback triggered — user: %s (@%s) — text: %.60s", first_name, username, user_text)

    # Notify admin before waiting on Gemini so the notification arrives quickly
    await notify_admin(context, update.effective_user, user_text)

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
        # Guard: response.text is None when Gemini blocks the content
        ai_reply = (response.text or "").strip()
        if not ai_reply:
            ai_reply = "I'm sorry, I couldn't generate a response for that. Please try rephrasing."
        logger.info("Gemini replied (%d chars)", len(ai_reply))

    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        ai_reply = (
            "⚠️ The AI couldn't respond right now. "
            "Please try again in a moment, or tap a button below for quick help."
        )

    await update.message.reply_text(ai_reply, reply_markup=MAIN_KEYBOARD)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Register handlers in priority order and start polling."""

    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Add it in Replit Secrets.")
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not set. Add it in Replit Secrets.")

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    # 1. Commands  (/start, /help, /myid)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CommandHandler("myid",  myid_command))

    # 2. Inline-button callbacks  (highest priority for button presses)
    app.add_handler(CallbackQueryHandler(cb_products, pattern="^products$"))
    app.add_handler(CallbackQueryHandler(cb_prices,   pattern="^prices$"))
    app.add_handler(CallbackQueryHandler(cb_contact,  pattern="^contact_support$"))
    app.add_handler(CallbackQueryHandler(cb_about,    pattern="^about_us$"))

    # 3. Text-pattern handlers  (old reply-keyboard button labels / typed labels)
    #    bot.hears() equivalent in python-telegram-bot
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^🛍️?\s*products?$"),       txt_products))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^💰?\s*prices?$"),          txt_prices))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^🎧?\s*contact\s+support$"), txt_contact))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^ℹ️?\s*about\s+us$"),       txt_about))

    # 4. Gemini fallback  (everything else that is text and not a command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
