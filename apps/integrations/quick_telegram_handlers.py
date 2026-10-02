"""Compatibility imports for AurumWeb Fast Telegram bot handlers.

Bot implementation lives in :mod:`bots.telegram.quick_consult_handlers`.
"""

from bots.telegram.quick_consult_handlers import (
    build_quick_telegram_webhook_response,
    handle_quick_telegram_update,
    process_pending_quick_telegram_updates,
)

__all__ = [
    "build_quick_telegram_webhook_response",
    "handle_quick_telegram_update",
    "process_pending_quick_telegram_updates",
]
