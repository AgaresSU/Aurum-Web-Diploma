"""Compatibility imports for the main Telegram bot handlers.

Bot implementation lives in :mod:`bots.telegram.main_handlers`.
"""

from bots.telegram.main_handlers import (
    TELEGRAM_PENDING_LINK_TITLE,
    handle_telegram_update,
    process_pending_telegram_updates,
)

__all__ = ["TELEGRAM_PENDING_LINK_TITLE", "handle_telegram_update", "process_pending_telegram_updates"]
