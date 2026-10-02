"""Compatibility imports for Telegram bot clients.

Bot implementation lives in :mod:`bots.telegram.clients`.
"""

from urllib import request

from bots.telegram.clients import QuickConsultationBotClient, TelegramBotClient

__all__ = ["QuickConsultationBotClient", "TelegramBotClient", "request"]
