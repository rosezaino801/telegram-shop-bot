"""
Render entry point — Muhammed Fashion Store Telegram bot.

Architecture
------------
Render kills a web-service process that doesn't bind $PORT within 60 s.
We satisfy that requirement with a minimal asyncio health-check server
(GET / → 200 OK) that runs in the MAIN thread's event loop.

The Telegram bot runs in a DEDICATED THREAD using PTB's built-in
`app.run_polling()`.  This is intentional:

• `run_polling()` is PTB's battle-tested method: it handles NetworkError
  retries, Conflict resolution, graceful SIGTERM shutdown, and proper
  asyncio event-loop lifecycle — all internally.

• The previous approach (`app.updater.start_polling()` inside the main
  asyncio loop) started polling as a background task.  If that task died
  silently, `stop_event.wait()` kept the coroutine alive so the health
  check returned OK — but the bot processed zero updates.  There was no
  way to detect or recover from that failure.

Failure model
-------------
If the polling thread exits for any reason (fatal exception, PTB shutdown),
the health-check coroutine detects it within 5 s and calls sys.exit(1).
Render sees the non-zero exit code and restarts the service automatically.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading

# ── Make telegram-bot/ importable ─────────────────────────────────────────────
BOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

# ── Logging (configure before bot.py imports so all loggers inherit it) ───────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from bot import build_app       # noqa: E402  (after sys.path tweak)
from telegram import Update     # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# ── Polling thread ────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# Set when the polling thread exits (for any reason).
_polling_done = threading.Event()


def _polling_thread() -> None:
    """
    Run PTB's run_polling() in its own thread (and its own asyncio event loop).

    run_polling() blocks until a shutdown signal is received or a fatal error
    occurs.  On return (normal or exception), _polling_done is set so the
    health-check loop can detect the failure and exit the process.
    """
    try:
        app = build_app()
        logger.info("[bot] run_polling() starting…")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,   # clear stale updates / webhook queue
        )
        logger.info("[bot] run_polling() returned — polling has stopped.")
    except Exception:
        logger.critical("[bot] Polling thread crashed.", exc_info=True)
    finally:
        _polling_done.set()


# ─────────────────────────────────────────────────────────────────────────────
# ── Async health-check server ─────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def _health_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Minimal HTTP/1.1 handler: any request → 200 OK."""
    try:
        await asyncio.wait_for(reader.read(4096), timeout=5.0)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"OK"
        )
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# ── Entry point ───────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

async def _main() -> None:
    port = int(os.environ.get("PORT", 8080))

    # Start the polling thread FIRST so it begins connecting to Telegram
    # while the health server binds the port (Render's 60 s deadline).
    t = threading.Thread(target=_polling_thread, name="bot-polling", daemon=True)
    t.start()

    # Bind the health-check port so Render marks the deploy as live.
    server = await asyncio.start_server(_health_handler, "0.0.0.0", port)
    addr = server.sockets[0].getsockname()
    logger.info("[health] Listening on %s:%s", addr[0], addr[1])

    async with server:
        # Monitor the polling thread; exit the process if it stops.
        while not _polling_done.is_set():
            await asyncio.sleep(5)

    # Polling thread is gone — exit with code 1 so Render restarts the service.
    logger.critical("[main] Polling thread stopped. Exiting (code 1) for Render restart.")
    sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except (KeyboardInterrupt, SystemExit) as exc:
        code = exc.code if isinstance(exc, SystemExit) else 0
        logger.info("[main] Process exiting (code %s).", code)
        sys.exit(code if isinstance(code, int) else 1)
