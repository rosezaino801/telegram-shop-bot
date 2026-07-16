"""
Telegram Customer Support Bot
================================
A clean, well-commented bot using python-telegram-bot v20+.
It greets users, displays a reply keyboard, and handles each menu option.
"""

import logging
import os

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
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
# Keyboard layout
# ---------------------------------------------------------------------------
# Each inner list is a row of buttons on the reply keyboard.
MAIN_KEYBOARD = [
    ["🛍️ Products", "💰 Prices"],
    ["🎧 Contact Support", "ℹ️ About Us"],
]

reply_markup = ReplyKeyboardMarkup(
    MAIN_KEYBOARD,
    resize_keyboard=True,   # shrinks the keyboard to fit the buttons
    one_time_keyboard=False, # keep the keyboard visible after a tap
)

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — Entry point for every new user.
    Sends a friendly welcome message and shows the main menu keyboard.
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
        "Tap a button below to get started 👇"
    )

    await update.message.reply_text(welcome_text, reply_markup=reply_markup)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /help — Shows available commands and keyboard options.
    """
    help_text = (
        "🆘 *Help Menu*\n\n"
        "Use the buttons below or type one of these commands:\n\n"
        "/start — Restart the bot and show the main menu\n"
        "/help  — Show this help message\n\n"
        "*Menu options:*\n"
        "🛍️ *Products* — Browse our product catalogue\n"
        "💰 *Prices*   — View our current pricing\n"
        "🎧 *Contact Support* — Reach a human agent\n"
        "ℹ️ *About Us* — Learn who we are\n"
    )

    await update.message.reply_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


# ---------------------------------------------------------------------------
# Message / button handlers
# ---------------------------------------------------------------------------

async def handle_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responds when the user taps the 'Products' button."""
    text = (
        "🛍️ *Our Products*\n\n"
        "Here's a quick overview of what we offer:\n\n"
        "• *Product A* — Premium quality, best-seller\n"
        "• *Product B* — Budget-friendly option\n"
        "• *Product C* — Limited edition, hurry!\n\n"
        "Want more details on a specific product? "
        "Tap 🎧 *Contact Support* and our team will help you out."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responds when the user taps the 'Prices' button."""
    text = (
        "💰 *Pricing*\n\n"
        "Here are our current prices:\n\n"
        "• *Product A* — $49.99\n"
        "• *Product B* — $19.99\n"
        "• *Product C* — $79.99 *(limited edition)*\n\n"
        "All prices include VAT. Bulk discounts available — "
        "contact support for details."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responds when the user taps the 'Contact Support' button."""
    text = (
        "🎧 *Contact Support*\n\n"
        "Our team is ready to help you!\n\n"
        "📧 *Email:* support@example.com\n"
        "🕐 *Hours:* Mon–Fri, 9 AM – 6 PM (UTC)\n\n"
        "You can also leave your question here and a team member "
        "will follow up with you shortly. We typically respond within "
        "*2 business hours*."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_about_us(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responds when the user taps the 'About Us' button."""
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
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fallback handler for any message that doesn't match a known button.
    Gently nudges the user back to the main menu.
    """
    text = (
        "🤔 I didn't quite get that.\n\n"
        "Please use the buttons below, or type /help to see what I can do."
    )
    await update.message.reply_text(text, reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Build and start the bot."""

    # Read the token from the environment variable set in Replit Secrets.
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Add it in the Replit Secrets panel."
        )

    # Build the Application (handles networking and dispatching).
    app = Application.builder().token(token).build()

    # --- Register command handlers ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # --- Register reply-keyboard button handlers ---
    # Each filter matches the exact button label text.
    app.add_handler(MessageHandler(filters.Regex(r"^🛍️ Products$"), handle_products))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Prices$"), handle_prices))
    app.add_handler(MessageHandler(filters.Regex(r"^🎧 Contact Support$"), handle_contact_support))
    app.add_handler(MessageHandler(filters.Regex(r"^ℹ️ About Us$"), handle_about_us))

    # --- Fallback: catch all other text messages ---
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown))

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Start polling Telegram for updates (blocks until interrupted).
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
