"""
Render entry point for Muhammed Fashion Store Telegram bot.

Render's free-tier web service kills any process that doesn't bind a port
within 60 seconds.  This file satisfies that requirement by starting a
minimal HTTP health-check server on $PORT in a background daemon thread,
then handing control to the bot's main() function.

Project layout
--------------
main.py              ← this file (Render start command: python main.py)
telegram-bot/bot.py  ← all bot logic; exposes main()
"""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Make `telegram-bot/` importable as a plain package directory.
BOT_DIR = os.path.join(os.path.dirname(__file__), "telegram-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)


# ── Tiny HTTP health-check server ─────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    """Respond to GET / with 200 OK so Render's health check passes."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):  # silence access logs
        pass


def _start_health_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    print(f"[health] Listening on port {port}", flush=True)
    server.serve_forever()


# ── Start health server in a daemon thread, then run the bot ──────────────────

if __name__ == "__main__":
    t = threading.Thread(target=_start_health_server, daemon=True)
    t.start()

    # Import and run the bot.  bot.py's main() calls app.run_polling() which
    # blocks forever, keeping this process (and the daemon thread) alive.
    from bot import main as bot_main
    bot_main()
