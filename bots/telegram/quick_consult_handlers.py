from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.integrations.models import QuickConsultationBotSettings
from apps.leads.models import QuickConsultation
from apps.leads.services import (
    accept_quick_offer,
    accept_quick_privacy,
    back_to_quick_offer,
    back_to_quick_privacy,
    cancel_quick_consultation,
    confirm_quick_email,
    create_quick_payment_from_client_amount,
    create_quick_payment_from_fixed_amount,
    latest_active_quick_consultation,
    parse_quick_payment_amount,
    pending_quick_consultation,
    pending_quick_email,
    pending_quick_email_confirm,
    pending_quick_legal,
    pending_quick_payment,
    set_quick_email,
    start_quick_consultation,
    start_quick_email_update,
    start_quick_entry,
    start_quick_payment,
    submit_quick_consultation_question,
    successful_quick_payments,
)

from .clients import QuickConsultationBotClient

CARD_MENU = "aurumweb-fast-menu.png"
CARD_OFFER = "aurumweb-fast-offer.png"
CARD_PRIVACY = "aurumweb-fast-privacy.png"
CARD_EMAIL = "aurumweb-fast-email.png"
CARD_CONTACTS = "aurumweb-fast-contacts.png"
CARD_CONSULTATION = "aurumweb-fast-consultation.png"
CARD_AMOUNT = "aurumweb-fast-amount.png"
CARD_PAYMENTS = "aurumweb-fast-payments.png"

MENU_TEXT = (
    "AurumWeb Fast\n"
    "Быстрые услуги по сайтам, поддержке, Python-автоматизации и Telegram-ботам. "
    "Выберите действие ниже."
)
CONSULTATION_TEXT = (
    "AurumWeb Fast\n\n"
    "Фиксированная консультация: 5 000 ₽\n"
    "Своя сумма: от 10 до 200 000 ₽\n"
    "После выбора суммы я сформирую счет и пришлю кнопку оплаты."
)
OFFER_TEXT = (
    "Публичная оферта\n"
    "Для оплаты консультации нужно принять публичную оферту AurumWeb. "
    "В ней описаны услуги, порядок оплаты, возврата и выполнения работ."
)
PRIVACY_TEXT = (
    "Политика обработки персональных данных\n"
    "Для уведомлений, документов и связи по консультации нужно согласие на обработку персональных данных."
)
EMAIL_TEXT = "Email для уведомлений\n" "Введите email для уведомлений об оплате и документов по консультации."
EMAIL_INVALID_TEXT = "Похоже, это не email. Введите адрес в формате name@example.ru."
EMAIL_CONFIRM_TEXT = (
    "Проверьте email\n"
    "Уведомления по консультации будут отправляться на: {email}\n"
    "Если адрес указан с ошибкой, уведомления могут не прийти."
)
QUESTION_PROMPT_TEXT = (
    "Опишите вопрос одним сообщением. Можно коротко: что нужно разобрать, какая цель и где сейчас стопор."
)
CANCELLED_TEXT = "Ок, сценарий быстрой консультации отменен. Если понадобится, запустите его заново."
ACCEPTED_TEXT = "Заявка на быструю консультацию принята. Я посмотрю вопрос и отвечу здесь в Telegram."
CONTACTS_TEXT = "Контакты AurumWeb\n" "Сайт: https://www.aurumweb.ru\n" "Email: aurumweb@aurumweb.ru"
PAYMENT_AMOUNT_PROMPT_TEXT = (
    "Своя сумма\n\n"
    "Введите сумму целым числом от 10 до 200 000 ₽. "
    "После этого я сформирую счет и пришлю кнопку оплаты."
)
PAYMENT_AMOUNT_CALLBACK_TEXT = "Введите сумму сообщением от 10 до 200 000 ₽."
PAYMENT_AMOUNT_INVALID_TEXT = "Не смог распознать сумму. Введите целое число от 10 до 200000, например 5000."
PAYMENT_CREATED_TEXT = (
    "Счет за услугу AurumWeb сформирован.\n"
    "Услуга: {item_name}\n"
    "Сумма: {amount} ₽\n"
    "Нажмите кнопку ниже, чтобы перейти к оплате через Robokassa."
)
PAYMENT_LINK_MISSING_TEXT = (
    "Счет за услугу AurumWeb сформирован.\n"
    "Услуга: {item_name}\n"
    "Сумма: {amount} ₽\n"
    "Платежная ссылка пока не готова. Я проверю настройки оплаты и пришлю ссылку повторно."
)

CONSULT_ACTIONS = {"проконсультироваться", "получить консультацию"}
PAY_ACTIONS = {"оплатить консультацию", "оплата консультации", "оплатить услугу", "оплата услуги"}
CANCEL_ACTIONS = {"отмена", "отменить", "отклонить"}


def _extract_message(update):
    for key in ("message", "edited_message"):
        message = update.get(key)
        if message:
            return message
    callback = update.get("callback_query", {})
    if callback.get("message"):
        return callback["message"]
    return None


def _extract_callback(update):
    return update.get("callback_query") or {}


def _callback_data(callback):
    return str((callback or {}).get("data") or "").strip()


def _chat_id(message):
    chat = message.get("chat") or {}
    value = chat.get("id")
    return str(value) if value is not None else ""


def _command_parts(text):
    parts = str(text or "").strip().split(maxsplit=1)
    command = parts[0].lower().split("@", 1)[0] if parts else ""
    payload = parts[1].strip() if len(parts) > 1 else ""
    return command, payload


def _normalized_action(text):
    return " ".join(str(text or "").strip().lower().split())


def _is_consult_action(text):
    return _normalized_action(text) in CONSULT_ACTIONS


def _is_pay_action(text):
    return _normalized_action(text) in PAY_ACTIONS


def _is_cancel_action(text):
    return _normalized_action(text) in CANCEL_ACTIONS


def _is_email(text):
    value = str(text or "").strip()
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def _payment_text(amount, item_name="Услуга AurumWeb"):
    return PAYMENT_CREATED_TEXT.format(amount=amount, item_name=item_name or "Услуга AurumWeb")


def _send_message_method(chat_id, text, reply_markup=None):
    payload = {"method": "sendMessage", "chat_id": str(chat_id), "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload


def _send_card_method(client, chat_id, filename, caption, reply_markup=None):
    payload = {
        "method": "sendPhoto",
        "chat_id": str(chat_id),
        "photo": client.quick_card_url(filename),
        "caption": caption,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload


def _answer_callback_method(callback, text=""):
    callback_id = (callback or {}).get("id")
    if not callback_id:
        return None
    payload = {"method": "answerCallbackQuery", "callback_query_id": callback_id}
    if text:
        payload["text"] = text
    return payload


def _callback_response(callback, deferred_method=None, notify_consultation_id=None, handled=True, text=""):
    return {
        "telegram_method": _answer_callback_method(callback, text),
        "deferred_telegram_method": deferred_method,
        "notify_consultation_id": notify_consultation_id,
        "handled": handled,
    }


def _callback_message(message, callback):
    return {**message, "from": callback.get("from") or message.get("from") or {}}


def _menu_method(chat_id, client):
    return _send_card_method(
        chat_id=chat_id,
        client=client,
        filename=CARD_MENU,
        caption=MENU_TEXT,
        reply_markup=client.quick_main_menu_keyboard(),
    )


def _offer_method(chat_id, client):
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_OFFER,
        caption=OFFER_TEXT,
        reply_markup=client.quick_offer_keyboard(),
    )


def _privacy_method(chat_id, client):
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_PRIVACY,
        caption=PRIVACY_TEXT,
        reply_markup=client.quick_privacy_keyboard(),
    )


def _email_method(chat_id, client, text=EMAIL_TEXT, keyboard=None):
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_EMAIL,
        caption=text,
        reply_markup=keyboard or client.quick_email_keyboard(),
    )


def _contacts_method(chat_id, client):
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_CONTACTS,
        caption=CONTACTS_TEXT,
        reply_markup=client.quick_contacts_keyboard(),
    )


def _consultation_choice_method(chat_id, client):
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_CONSULTATION,
        caption=CONSULTATION_TEXT,
        reply_markup=client.quick_consultation_offer_keyboard(),
    )


def _question_method(chat_id, client):
    return _send_message_method(
        chat_id,
        QUESTION_PROMPT_TEXT,
        reply_markup=client.quick_consultation_cancel_keyboard(),
    )


def _amount_method(chat_id, client):
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_AMOUNT,
        caption=PAYMENT_AMOUNT_PROMPT_TEXT,
        reply_markup=client.quick_amount_keyboard(),
    )


def _payments_text(chat_id):
    paid_consultations = successful_quick_payments(chat_id)
    if not paid_consultations:
        return "Мои оплаты\n\nУ вас пока нет успешных оплат."
    lines = ["Мои оплаты", "", "Последние успешные оплаты:"]
    for consultation in paid_consultations:
        invoice = consultation.invoice
        paid_payment = invoice.payments.filter(status="succeeded").order_by("-paid_at").first()
        amount = paid_payment.amount if paid_payment else invoice.amount
        lines.append(f"Счет #{invoice.pk}: {invoice.title} — {amount} ₽ — оплачен")
    lines.append("")
    lines.append("Фискальные чеки отправляет Robokassa на email, указанный при оплате.")
    return "\n".join(lines)


def _payments_method(chat_id, client):
    consultation = latest_active_quick_consultation(chat_id)
    return _send_card_method(
        client=client,
        chat_id=chat_id,
        filename=CARD_PAYMENTS,
        caption=_payments_text(chat_id),
        reply_markup=client.quick_payments_keyboard(consultation),
    )


def _payment_link_method(chat_id, client, invoice, payment_url):
    if not payment_url:
        return _send_message_method(
            chat_id,
            PAYMENT_LINK_MISSING_TEXT.format(amount=invoice.amount, item_name=invoice.title),
            reply_markup=client.quick_amount_keyboard(),
        )
    return _send_message_method(
        chat_id,
        _payment_text(invoice.amount, invoice.title),
        reply_markup=client.quick_consultation_payment_keyboard(payment_url),
    )


def _payment_entry_method(chat_id, client, consultation):
    if consultation.status == QuickConsultation.Status.WAITING_PRIVACY:
        return _privacy_method(chat_id, client)
    if consultation.status == QuickConsultation.Status.WAITING_EMAIL:
        return _email_method(chat_id, client)
    if consultation.status == QuickConsultation.Status.WAITING_EMAIL_CONFIRM:
        text = EMAIL_TEXT
        keyboard = client.quick_email_keyboard()
        if consultation.client_email:
            text = EMAIL_CONFIRM_TEXT.format(email=consultation.client_email)
            keyboard = client.quick_email_confirm_keyboard()
        return _email_method(chat_id, client, text=text, keyboard=keyboard)
    if consultation.status == QuickConsultation.Status.WAITING_PAYMENT_AMOUNT:
        return _consultation_choice_method(chat_id, client)
    if consultation.status == QuickConsultation.Status.WAITING_QUESTION:
        return _question_method(chat_id, client)
    if consultation.status == QuickConsultation.Status.CLOSED:
        return _menu_method(chat_id, client)
    return _offer_method(chat_id, client)


def _payment_entry_step(consultation):
    return {
        QuickConsultation.Status.WAITING_OFFER: "waiting_offer",
        QuickConsultation.Status.WAITING_PRIVACY: "waiting_privacy",
        QuickConsultation.Status.WAITING_EMAIL: "waiting_email",
        QuickConsultation.Status.WAITING_EMAIL_CONFIRM: "waiting_email_confirm",
        QuickConsultation.Status.WAITING_PAYMENT_AMOUNT: "waiting_payment_choice",
        QuickConsultation.Status.WAITING_QUESTION: "waiting_question",
        QuickConsultation.Status.CLOSED: "menu",
    }.get(consultation.status, "waiting_offer")


def _intro(chat_id, message=None):
    client = QuickConsultationBotClient()
    consultation = start_quick_entry(chat_id, message or {"chat": {"id": chat_id}})
    sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
    return {
        "ok": bool(sent),
        "handled": True,
        "quick_consultation": True,
        "step": _payment_entry_step(consultation),
        "quick_consultation_id": consultation.pk,
    }


def _start(chat_id, message):
    consultation = start_quick_consultation(chat_id, message)
    client = QuickConsultationBotClient()
    sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
    return {
        "ok": bool(sent),
        "handled": True,
        "quick_consultation": True,
        "step": _payment_entry_step(consultation),
        "quick_consultation_id": consultation.pk,
    }


def _pay(chat_id, message):
    consultation = start_quick_payment(chat_id, message)
    client = QuickConsultationBotClient()
    sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
    return {
        "ok": bool(sent),
        "handled": True,
        "quick_consultation": True,
        "step": _payment_entry_step(consultation),
        "quick_consultation_id": consultation.pk,
    }


def _cancel(chat_id):
    consultation = cancel_quick_consultation(chat_id)
    sent = QuickConsultationBotClient().send_message(CANCELLED_TEXT, chat_id=chat_id)
    return {
        "ok": bool(sent),
        "handled": True,
        "quick_consultation": True,
        "step": "cancelled",
        "quick_consultation_id": consultation.pk if consultation else None,
    }


def _submit_payment(chat_id, message, text):
    amount = parse_quick_payment_amount(text)
    client = QuickConsultationBotClient()
    if amount is None:
        sent = client.send_message(
            PAYMENT_AMOUNT_INVALID_TEXT,
            chat_id=chat_id,
            reply_markup=client.quick_consultation_cancel_keyboard(),
        )
        return {"ok": bool(sent), "handled": True, "quick_consultation": True, "step": "payment_amount_invalid"}
    consultation, invoice, payment_url = create_quick_payment_from_client_amount(chat_id, message, amount)
    if invoice is None:
        sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
        return {
            "ok": bool(sent),
            "handled": True,
            "quick_consultation": True,
            "step": _payment_entry_step(consultation),
            "quick_consultation_id": consultation.pk,
        }
    sent = client.send_message(
        _payment_text(invoice.amount, invoice.title),
        chat_id=chat_id,
        reply_markup=client.quick_consultation_payment_keyboard(payment_url),
    )
    return {
        "ok": bool(sent),
        "handled": True,
        "quick_consultation": True,
        "step": "payment_link_created",
        "quick_consultation_id": consultation.pk,
        "invoice_id": invoice.pk,
    }


def _submit(chat_id, message, text):
    consultation = submit_quick_consultation_question(chat_id, message, text)
    client = QuickConsultationBotClient()
    client.notify_quick_consultation_created(consultation)
    sent = client.send_message(ACCEPTED_TEXT, chat_id=chat_id)
    return {
        "ok": bool(sent),
        "handled": True,
        "quick_consultation": True,
        "step": "created",
        "quick_consultation_id": consultation.pk,
    }


def handle_quick_telegram_update(update):
    callback = _extract_callback(update)
    message = _extract_message(update)
    if not message:
        return {"ok": True, "handled": False, "reason": "message_missing"}
    chat_id = _chat_id(message)
    if not chat_id:
        return {"ok": True, "handled": False, "reason": "chat_missing"}

    client = QuickConsultationBotClient()
    if callback:
        callback_message = _callback_message(message, callback)
        data = _callback_data(callback)
        if data != "quick:custom_amount":
            client.answer_callback_query(callback.get("id"))
        if data == "quick:menu":
            consultation = start_quick_entry(chat_id, callback_message)
            sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": _payment_entry_step(consultation),
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:start":
            return _start(chat_id, callback_message)
        if data == "quick:pay":
            return _pay(chat_id, callback_message)
        if data == "quick:accept_offer":
            consultation = accept_quick_offer(chat_id, callback_message)
            sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": _payment_entry_step(consultation),
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:accept_privacy":
            consultation = accept_quick_privacy(chat_id, callback_message)
            sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": _payment_entry_step(consultation),
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:confirm_email":
            consultation = confirm_quick_email(chat_id, callback_message)
            sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": _payment_entry_step(consultation),
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:edit_email":
            consultation = start_quick_email_update(chat_id, callback_message)
            text = EMAIL_TEXT
            if consultation.client_email:
                text = f"Изменение email\n\nВведите новый email для уведомлений.\nТекущий email: {consultation.client_email}"
            sent = client.send_quick_card(CARD_EMAIL, text, chat_id, client.quick_email_keyboard())
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": "waiting_email",
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:back_offer":
            consultation = back_to_quick_offer(chat_id, callback_message)
            sent = client.send_quick_card(CARD_OFFER, OFFER_TEXT, chat_id, client.quick_offer_keyboard())
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": "waiting_offer",
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:back_privacy":
            consultation = back_to_quick_privacy(chat_id, callback_message)
            sent = client.send_quick_card(CARD_PRIVACY, PRIVACY_TEXT, chat_id, client.quick_privacy_keyboard())
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": "waiting_privacy",
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:pay_fixed":
            consultation = start_quick_payment(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.WAITING_PAYMENT_AMOUNT:
                sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
                return {
                    "ok": bool(sent),
                    "handled": True,
                    "quick_consultation": True,
                    "step": _payment_entry_step(consultation),
                    "quick_consultation_id": consultation.pk,
                }
            consultation, invoice, payment_url = create_quick_payment_from_fixed_amount(chat_id, callback_message)
            sent = client.send_prepared_method(_payment_link_method(chat_id, client, invoice, payment_url))
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": "payment_link_created",
                "quick_consultation_id": consultation.pk,
                "invoice_id": invoice.pk,
            }
        if data == "quick:custom_amount":
            consultation = start_quick_payment(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.WAITING_PAYMENT_AMOUNT:
                client.answer_callback_query(callback.get("id"))
                sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
                return {
                    "ok": bool(sent),
                    "handled": True,
                    "quick_consultation": True,
                    "step": _payment_entry_step(consultation),
                    "quick_consultation_id": consultation.pk,
                }
            client.answer_callback_query(callback.get("id"), PAYMENT_AMOUNT_CALLBACK_TEXT)
            sent = client.send_quick_card(
                CARD_AMOUNT, PAYMENT_AMOUNT_PROMPT_TEXT, chat_id, client.quick_amount_keyboard()
            )
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": "waiting_payment_amount",
                "quick_consultation_id": consultation.pk,
            }
        if data == "quick:payments":
            consultation = start_quick_entry(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.CLOSED:
                sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
                return {
                    "ok": bool(sent),
                    "handled": True,
                    "quick_consultation": True,
                    "step": _payment_entry_step(consultation),
                    "quick_consultation_id": consultation.pk,
                }
            sent = client.send_quick_card(
                CARD_PAYMENTS,
                _payments_text(chat_id),
                chat_id,
                client.quick_payments_keyboard(latest_active_quick_consultation(chat_id)),
            )
            return {"ok": bool(sent), "handled": True, "quick_consultation": True, "step": "payments"}
        if data == "quick:contacts":
            consultation = start_quick_entry(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.CLOSED:
                sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
                return {
                    "ok": bool(sent),
                    "handled": True,
                    "quick_consultation": True,
                    "step": _payment_entry_step(consultation),
                    "quick_consultation_id": consultation.pk,
                }
            sent = client.send_quick_card(CARD_CONTACTS, CONTACTS_TEXT, chat_id, client.quick_contacts_keyboard())
            return {"ok": bool(sent), "handled": True, "quick_consultation": True, "step": "contacts"}
        if data == "quick:cancel":
            return _cancel(chat_id)
        return _intro(chat_id, callback_message)

    text = (message.get("text") or "").strip()
    command, _payload = _command_parts(text)
    if _is_cancel_action(text):
        return _cancel(chat_id)
    if pending_quick_email(chat_id):
        if not _is_email(text):
            sent = client.send_message(EMAIL_INVALID_TEXT, chat_id=chat_id)
            return {"ok": bool(sent), "handled": True, "quick_consultation": True, "step": "email_invalid"}
        consultation = set_quick_email(chat_id, message, text)
        sent = client.send_quick_card(
            CARD_EMAIL,
            EMAIL_CONFIRM_TEXT.format(email=consultation.client_email),
            chat_id,
            client.quick_email_confirm_keyboard(),
        )
        return {
            "ok": bool(sent),
            "handled": True,
            "quick_consultation": True,
            "step": "waiting_email_confirm",
            "quick_consultation_id": consultation.pk,
        }
    if pending_quick_email_confirm(chat_id) and _is_email(text):
        consultation = set_quick_email(chat_id, message, text)
        sent = client.send_quick_card(
            CARD_EMAIL,
            EMAIL_CONFIRM_TEXT.format(email=consultation.client_email),
            chat_id,
            client.quick_email_confirm_keyboard(),
        )
        return {
            "ok": bool(sent),
            "handled": True,
            "quick_consultation": True,
            "step": "waiting_email_confirm",
            "quick_consultation_id": consultation.pk,
        }
    if pending_quick_payment(chat_id):
        return _submit_payment(chat_id, message, text)
    if _is_consult_action(text):
        return _start(chat_id, message)
    if _is_pay_action(text):
        return _pay(chat_id, message)
    if command in ("/start", "/help"):
        return _intro(chat_id, message)
    if command == "/consult":
        return _start(chat_id, message)
    if command == "/pay":
        return _pay(chat_id, message)
    if command == "/cancel":
        return _cancel(chat_id)
    consultation = pending_quick_legal(chat_id)
    if consultation:
        sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
        return {
            "ok": bool(sent),
            "handled": True,
            "quick_consultation": True,
            "step": _payment_entry_step(consultation),
            "quick_consultation_id": consultation.pk,
        }
    if pending_quick_consultation(chat_id):
        return _submit(chat_id, message, text)
    if text and not command.startswith("/"):
        consultation = start_quick_consultation(chat_id, message)
        if consultation.status != QuickConsultation.Status.WAITING_QUESTION:
            sent = client.send_prepared_method(_payment_entry_method(chat_id, client, consultation))
            return {
                "ok": bool(sent),
                "handled": True,
                "quick_consultation": True,
                "step": _payment_entry_step(consultation),
                "quick_consultation_id": consultation.pk,
            }
        return _submit(chat_id, message, text)
    return _intro(chat_id, message)


def build_quick_telegram_webhook_response(update):
    """Build a direct Telegram webhook response without an outbound API call."""

    callback = _extract_callback(update)
    message = _extract_message(update)
    if not message:
        return {"telegram_method": None, "notify_consultation_id": None, "handled": False}
    chat_id = _chat_id(message)
    if not chat_id:
        return {"telegram_method": None, "notify_consultation_id": None, "handled": False}

    client = QuickConsultationBotClient()
    if callback:
        callback_message = _callback_message(message, callback)
        data = _callback_data(callback)
        if data == "quick:menu":
            consultation = start_quick_entry(chat_id, callback_message)
            return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
        if data == "quick:start":
            consultation = start_quick_consultation(chat_id, callback_message)
            return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
        if data == "quick:pay":
            consultation = start_quick_payment(chat_id, callback_message)
            return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
        if data == "quick:accept_offer":
            consultation = accept_quick_offer(chat_id, callback_message)
            return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
        if data == "quick:accept_privacy":
            consultation = accept_quick_privacy(chat_id, callback_message)
            return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
        if data == "quick:confirm_email":
            consultation = confirm_quick_email(chat_id, callback_message)
            return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
        if data == "quick:edit_email":
            consultation = start_quick_email_update(chat_id, callback_message)
            text = EMAIL_TEXT
            if consultation.client_email:
                text = f"Изменение email\n\nВведите новый email для уведомлений.\nТекущий email: {consultation.client_email}"
            return _callback_response(callback, _email_method(chat_id, client, text=text))
        if data == "quick:back_offer":
            back_to_quick_offer(chat_id, callback_message)
            return _callback_response(callback, _offer_method(chat_id, client))
        if data == "quick:back_privacy":
            back_to_quick_privacy(chat_id, callback_message)
            return _callback_response(callback, _privacy_method(chat_id, client))
        if data == "quick:pay_fixed":
            consultation = start_quick_payment(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.WAITING_PAYMENT_AMOUNT:
                return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
            _consultation, invoice, payment_url = create_quick_payment_from_fixed_amount(chat_id, callback_message)
            return _callback_response(callback, _payment_link_method(chat_id, client, invoice, payment_url))
        if data == "quick:custom_amount":
            consultation = start_quick_payment(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.WAITING_PAYMENT_AMOUNT:
                return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
            return _callback_response(
                callback,
                _amount_method(chat_id, client),
                text=PAYMENT_AMOUNT_CALLBACK_TEXT,
            )
        if data == "quick:payments":
            consultation = start_quick_entry(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.CLOSED:
                return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
            return _callback_response(callback, _payments_method(chat_id, client))
        if data == "quick:contacts":
            consultation = start_quick_entry(chat_id, callback_message)
            if consultation.status != QuickConsultation.Status.CLOSED:
                return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))
            return _callback_response(callback, _contacts_method(chat_id, client))
        if data == "quick:cancel":
            cancel_quick_consultation(chat_id)
            return _callback_response(callback, _send_message_method(chat_id, CANCELLED_TEXT))
        consultation = start_quick_entry(chat_id, callback_message)
        return _callback_response(callback, _payment_entry_method(chat_id, client, consultation))

    text = (message.get("text") or "").strip()
    command, _payload = _command_parts(text)
    if _is_cancel_action(text):
        cancel_quick_consultation(chat_id)
        return {
            "telegram_method": _send_message_method(chat_id, CANCELLED_TEXT),
            "notify_consultation_id": None,
            "handled": True,
        }
    if pending_quick_email(chat_id):
        if not _is_email(text):
            return {
                "telegram_method": _send_message_method(chat_id, EMAIL_INVALID_TEXT),
                "notify_consultation_id": None,
                "handled": True,
            }
        consultation = set_quick_email(chat_id, message, text)
        return {
            "telegram_method": _email_method(
                chat_id,
                client,
                text=EMAIL_CONFIRM_TEXT.format(email=consultation.client_email),
                keyboard=client.quick_email_confirm_keyboard(),
            ),
            "notify_consultation_id": None,
            "handled": True,
        }
    if pending_quick_email_confirm(chat_id) and _is_email(text):
        consultation = set_quick_email(chat_id, message, text)
        return {
            "telegram_method": _email_method(
                chat_id,
                client,
                text=EMAIL_CONFIRM_TEXT.format(email=consultation.client_email),
                keyboard=client.quick_email_confirm_keyboard(),
            ),
            "notify_consultation_id": None,
            "handled": True,
        }
    if pending_quick_payment(chat_id):
        amount = parse_quick_payment_amount(text)
        if amount is None:
            return {
                "telegram_method": _send_message_method(
                    chat_id,
                    PAYMENT_AMOUNT_INVALID_TEXT,
                    reply_markup=client.quick_consultation_cancel_keyboard(),
                ),
                "notify_consultation_id": None,
                "handled": True,
            }
        _consultation, invoice, payment_url = create_quick_payment_from_client_amount(chat_id, message, amount)
        if invoice is None:
            return {
                "telegram_method": _payment_entry_method(chat_id, client, _consultation),
                "notify_consultation_id": None,
                "handled": True,
            }
        return {
            "telegram_method": _send_message_method(
                chat_id,
                _payment_text(invoice.amount, invoice.title),
                reply_markup=client.quick_consultation_payment_keyboard(payment_url),
            ),
            "notify_consultation_id": None,
            "handled": True,
        }
    if _is_consult_action(text):
        consultation = start_quick_consultation(chat_id, message)
        return {
            "telegram_method": _payment_entry_method(chat_id, client, consultation),
            "notify_consultation_id": None,
            "handled": True,
        }
    if _is_pay_action(text):
        consultation = start_quick_payment(chat_id, message)
        return {
            "telegram_method": _payment_entry_method(chat_id, client, consultation),
            "notify_consultation_id": None,
            "handled": True,
        }
    if command in ("/start", "/help"):
        consultation = start_quick_entry(chat_id, message)
        return {
            "telegram_method": _payment_entry_method(chat_id, client, consultation),
            "notify_consultation_id": None,
            "handled": True,
        }
    if command == "/consult":
        consultation = start_quick_consultation(chat_id, message)
        return {
            "telegram_method": _payment_entry_method(chat_id, client, consultation),
            "notify_consultation_id": None,
            "handled": True,
        }
    if command == "/pay":
        consultation = start_quick_payment(chat_id, message)
        return {
            "telegram_method": _payment_entry_method(chat_id, client, consultation),
            "notify_consultation_id": None,
            "handled": True,
        }
    if command == "/cancel":
        cancel_quick_consultation(chat_id)
        return {
            "telegram_method": _send_message_method(chat_id, CANCELLED_TEXT),
            "notify_consultation_id": None,
            "handled": True,
        }
    consultation = pending_quick_legal(chat_id)
    if consultation:
        return {
            "telegram_method": _payment_entry_method(chat_id, client, consultation),
            "notify_consultation_id": None,
            "handled": True,
        }
    if pending_quick_consultation(chat_id):
        consultation = submit_quick_consultation_question(chat_id, message, text)
        return {
            "telegram_method": _send_message_method(chat_id, ACCEPTED_TEXT),
            "notify_consultation_id": consultation.pk,
            "handled": True,
        }
    if text and not command.startswith("/"):
        consultation = start_quick_consultation(chat_id, message)
        if consultation.status != QuickConsultation.Status.WAITING_QUESTION:
            return {
                "telegram_method": _payment_entry_method(chat_id, client, consultation),
                "notify_consultation_id": None,
                "handled": True,
            }
        consultation = submit_quick_consultation_question(chat_id, message, text)
        return {
            "telegram_method": _send_message_method(chat_id, ACCEPTED_TEXT),
            "notify_consultation_id": consultation.pk,
            "handled": True,
        }
    consultation = start_quick_entry(chat_id, message)
    return {
        "telegram_method": _payment_entry_method(chat_id, client, consultation),
        "notify_consultation_id": None,
        "handled": True,
    }


def process_pending_quick_telegram_updates(limit=20):
    config = QuickConsultationBotSettings.load()
    client = QuickConsultationBotClient()
    offset = config.last_update_id + 1 if config.last_update_id is not None else None
    updates = client.get_updates(limit=limit, offset=offset)
    handled = []
    max_update_id = config.last_update_id
    for update in updates.get("result", []):
        update_id = update.get("update_id")
        if update_id is not None and (max_update_id is None or update_id > max_update_id):
            max_update_id = update_id
        handled.append(handle_quick_telegram_update(update))
    if max_update_id != config.last_update_id:
        config.last_update_id = max_update_id
        config.save(update_fields=("last_update_id", "updated_at"))
    return {
        "ok": bool(updates.get("ok")),
        "updates": len(updates.get("result", [])),
        "handled": handled,
        "last_update_id": config.last_update_id,
    }
