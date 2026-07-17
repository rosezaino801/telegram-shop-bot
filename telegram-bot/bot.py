"""
Telegram Customer Support Bot
================================
A clean, well-commented bot using python-telegram-bot v20+.

- Inline keyboard with Products, Prices, Contact Support, About Us buttons.
- Any free-text message (not a command or button press) is forwarded to
  OpenAI and the AI's reply is sent back to the user.
"""

import logging
import os

from openai import AsyncOpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
# OpenAI client
# ---------------------------------------------------------------------------
# AsyncOpenAI reads OPENAI_API_KEY from the environment automatically.
openai_client = AsyncOpenAI()

# System prompt that shapes how the AI responds inside this support bot.
SYSTEM_PROMPT = (
    "You are a friendly and helpful customer support assistant for our business. "
    "Answer questions clearly and concisely. "
    "If you don't know something, say so honestly and suggest the user contact "
    "the support team at support@example.com."
)

# ---------------------------------------------------------------------------
# Inline keyboard layout
# ---------------------------------------------------------------------------
MAIN_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("🛍️ Products", callback_data="products"),
            InlineKeyboardButton("💰 Prices",   callback_data="prices"),
        ],
        [
            InlineKeyboardButton("🎧 Contact Support", callback_data="contact_support"),
            InlineKeyboardButton("ℹ️ About Us",        callback_data="about_us"),
        ],
    ]
)

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — Entry point for every new user.
    Sends a friendly welcome message with the inline keyboard attached.
    """
    user = update.effective_user
    first_name = user.first_name if user else "there"

    welcome_text = (
        f"👋 Hello, {first_name}! Welcome to our Customer Support bot.\n\n"
        "I'm here to help you with:\n"
        "• Product information\n"
        "• Pricing details\n"
        "• Getting in touch with our support team\n"
        "• Learning more about us\n\n"
        "Tap a button below, or just type your question and I'll answer it 💬"
    )

    await update.message.reply_text(welcome_text, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — Shows available commands and inline menu."""
    help_text = (
        "🆘 *Help Menu*\n\n"
        "Use the inline buttons below or type one of these commands:\n\n"
        "/start — Restart the bot and show the main menu\n"
        "/help  — Show this help message\n\n"
        "*Menu options:*\n"
        "🛍️ *Products* — Browse our product catalogue\n"
        "💰 *Prices*   — View our current pricing\n"
        "🎧 *Contact Support* — Reach a human agent\n"
        "ℹ️ *About Us* — Learn who we are\n\n"
        "Or just *type any question* and our AI assistant will help you! 🤖"
    )

    await update.message.reply_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


# ---------------------------------------------------------------------------
# Callback query handlers (inline button presses)
# ---------------------------------------------------------------------------

async def handle_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when the user taps the 🛍️ Products inline button."""
    query = update.callback_query
    await query.answer()

    text = (
        "🛍️ *Our Products*\n\n"
        "Here's a quick overview of what we offer:\n\n"
        "• *Product A* — Premium quality, best-seller\n"
        "• *Product B* — Budget-friendly option\n"
        "• *Product C* — Limited edition, hurry!\n\n"
        "Want more details on a specific product? "
        "Tap 🎧 *Contact Support* and our team will help you out."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def handle_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when the user taps the 💰 Prices inline button."""
    query = update.callback_query
    await query.answer()

    text = (
        "💰 *Pricing*\n\n"
        "Here are our current prices:\n\n"
        "• *Product A* — $49.99\n"
        "• *Product B* — $19.99\n"
        "• *Product C* — $79.99 *(limited edition)*\n\n"
        "All prices include VAT. Bulk discounts available — "
        "contact support for details."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def handle_contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when the user taps the 🎧 Contact Support inline button."""
    query = update.callback_query
    await query.answer()

    text = (
        "🎧 *Contact Support*\n\n"
        "Our team is ready to help you!\n\n"
        "📧 *Email:* support@example.com\n"
        "🕐 *Hours:* Mon–Fri, 9 AM – 6 PM (UTC)\n\n"
        "You can also leave your question here and a team member "
        "will follow up with you shortly. We typically respond within "
        "*2 business hours*."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def handle_about_us(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when the user taps the ℹ️ About Us inline button."""
    query = update.callback_query
    await query.answer()

    text = (
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
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# AI fallback handler
# ---------------------------------------------------------------------------

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles any free-text message that isn't a command or button press.
    Sends the user's message to OpenAI and replies with the AI's response.
    """
    user_text = update.message.text

    # Show a typing indicator while we wait for the OpenAI response.
    await update.message.chat.send_action("typing")

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_text},
            ],
            max_tokens=1024,
        )

        ai_reply = response.choices[0].message.content.strip()
        logger.info("OpenAI replied (%d chars) to: %s", len(ai_reply), user_text[:60])

    except Exception as exc:
        logger.error("OpenAI request failed: %s", exc)
        ai_reply = (
            "⚠️ Sorry, I couldn't reach the AI right now. "
            "Please try again in a moment, or tap a button below for quick help."
        )

    # Send the AI reply and keep the inline menu visible.
    await update.message.reply_text(ai_reply, reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Build and start the bot."""

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Add it in the Replit Secrets panel."
        )

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Add it in the Replit Secrets panel."
        )

    app = Application.builder().token(token).build()

    # --- Command handlers ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # --- Inline button handlers ---
    app.add_handler(CallbackQueryHandler(handle_products,        pattern="^products$"))
    app.add_handler(CallbackQueryHandler(handle_prices,          pattern="^prices$"))
    app.add_handler(CallbackQueryHandler(handle_contact_support, pattern="^contact_support$"))
    app.add_handler(CallbackQueryHandler(handle_about_us,        pattern="^about_us$"))

    # --- AI fallback: any text that isn't a command goes to OpenAI ---
    # This handler runs AFTER all the above, so button presses are never
    # caught here (they arrive as CallbackQuery updates, not text messages).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
