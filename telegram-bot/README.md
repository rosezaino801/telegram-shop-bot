# Telegram Customer Support Bot

A clean Python bot built with [python-telegram-bot](https://python-telegram-bot.org/) v20+.

## Features

- `/start` — Welcome message + reply keyboard
- `/help` — Command reference
- **Products** button — Product catalogue overview
- **Prices** button — Current pricing list
- **Contact Support** button — Support contact details
- **About Us** button — Company information
- Fallback handler for unrecognised messages

## Setup

1. Get a bot token from [@BotFather](https://t.me/BotFather) on Telegram.
2. Add it as `TELEGRAM_BOT_TOKEN` in Replit Secrets.
3. Run the **Telegram Bot** workflow.

## Customising

| What to change | Where |
|---|---|
| Button labels | `MAIN_KEYBOARD` list in `bot.py` |
| Button responses | `handle_*` functions in `bot.py` |
| Product/price info | Text inside each handler |
| Support email / hours | `handle_contact_support()` |
