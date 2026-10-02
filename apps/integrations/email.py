from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .models import IntegrationEvent


def _clip(value, limit=1200):
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


class EmailNotificationClient:
    def __init__(self, enabled=None, admin_emails=None):
        self.enabled = settings.AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED if enabled is None else enabled
        self.admin_emails = list(settings.AURUMWEB_ADMIN_EMAILS if admin_emails is None else admin_emails)

    @property
    def configured(self):
        return bool(self.enabled and self.admin_emails)

    def _absolute_url(self, path):
        base_url = settings.AURUMWEB_SITE_URL.rstrip("/")
        return f"{base_url}{path}" if base_url else path

    def send(self, subject, body, recipients, payload=None):
        recipients = [email for email in recipients if email]
        if not self.enabled:
            return False
        if not recipients:
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.SYSTEM,
                status=IntegrationEvent.Status.WARNING,
                title="Email уведомление не отправлено",
                payload={"reason": "recipients_missing", **(payload or {})},
            )
            return False

        try:
            sent = send_mail(
                f"{settings.AURUMWEB_EMAIL_SUBJECT_PREFIX}{subject}",
                body,
                settings.DEFAULT_FROM_EMAIL,
                recipients,
                fail_silently=False,
            )
        except Exception as exc:
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.SYSTEM,
                status=IntegrationEvent.Status.ERROR,
                title="Ошибка email уведомления",
                payload={"error": str(exc), **(payload or {})},
            )
            return False

        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.SYSTEM,
            status=IntegrationEvent.Status.SUCCESS if sent else IntegrationEvent.Status.WARNING,
            title="Email уведомление отправлено" if sent else "Email уведомление не отправлено",
            payload={"recipients": recipients, "subject": subject, **(payload or {})},
        )
        return bool(sent)

    def notify_new_lead(self, lead, conversation=None):
        if not self.enabled:
            return False
        subject = f"Новая заявка #{lead.pk}: {lead.subject or lead.service_type or 'AurumWeb'}"
        office_path = reverse("office:lead-detail", kwargs={"pk": lead.pk})
        lines = [
            "Новая заявка AurumWeb",
            "",
            f"Имя: {lead.name or '-'}",
            f"Email: {lead.email or '-'}",
            f"Тип: {lead.service_type or '-'}",
            f"Тема: {lead.subject or '-'}",
            f"Заявка в Office: {self._absolute_url(office_path)}",
        ]
        if conversation:
            conversation_path = reverse("office:conversation-detail", kwargs={"pk": conversation.pk})
            lines.append(f"Диалог: {self._absolute_url(conversation_path)}")
        lines.extend(["", "Описание:", _clip(lead.task)])
        return self.send(subject, "\n".join(lines), self.admin_emails, {"lead_id": lead.pk})

    def notify_client_message(self, conversation, message):
        if not self.enabled:
            return False
        subject = f"Новое сообщение клиента: {conversation.title or f'диалог #{conversation.pk}'}"
        conversation_path = reverse("office:conversation-detail", kwargs={"pk": conversation.pk})
        lines = [
            "Клиент написал в диалоге AurumWeb",
            "",
            f"Диалог: {conversation.title or conversation.pk}",
            f"Email клиента: {conversation.client_email or '-'}",
            f"Открыть в Office: {self._absolute_url(conversation_path)}",
            "",
            "Сообщение:",
            _clip(message.body),
        ]
        return self.send(subject, "\n".join(lines), self.admin_emails, {"conversation_id": conversation.pk})

    def notify_invoice_issued(self, invoice):
        if not (self.enabled and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED):
            return False
        invoice_path = reverse("billing:invoice-detail", kwargs={"token": invoice.public_token})
        body = "\n".join(
            [
                "Счет AurumWeb выставлен.",
                "",
                f"Счет: {invoice.title}",
                f"Сумма: {invoice.amount} ₽",
                f"Открыть счет: {self._absolute_url(invoice_path)}",
                "",
                "Оплатить счет можно через Robokassa: банковской картой, СБП или QR-кодом.",
            ]
        )
        return self.send(
            f"Счет AurumWeb #{invoice.pk}",
            body,
            [invoice.client_email],
            {"invoice_id": invoice.pk},
        )

    def notify_payment_succeeded(self, payment):
        self.notify_payment_succeeded_admin(payment)
        self.notify_payment_succeeded_client(payment)
        return True

    def notify_payment_succeeded_admin(self, payment):
        invoice = payment.invoice
        if not self.enabled:
            return False
        return self.send(
            f"Оплата получена по счету #{invoice.pk}",
            "\n".join(
                [
                    "Robokassa подтвердила оплату.",
                    "",
                    f"Счет: {invoice.title}",
                    f"Сумма: {payment.amount} ₽",
                    f"Счет в Office: {self._absolute_url(reverse('office:invoice-detail', kwargs={'pk': invoice.pk}))}",
                ]
            ),
            self.admin_emails,
            {"invoice_id": invoice.pk, "payment_id": payment.pk},
        )

    def notify_payment_succeeded_client(self, payment):
        invoice = payment.invoice
        if not (self.enabled and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED):
            return False
        invoice_path = reverse("client_portal:invoice-detail", kwargs={"token": invoice.public_token})
        return self.send(
            f"Оплата получена по счету #{invoice.pk}",
            "\n".join(
                [
                    "Оплата по счету AurumWeb получена.",
                    "",
                    f"Счет: {invoice.title}",
                    f"Сумма: {payment.amount} ₽",
                    "Статус: оплачен",
                    "",
                    (
                        "Чек: Robokassa/Робочеки СМЗ формирует фискальный чек "
                        "и отправляет его отдельным письмом на email, указанный при оплате."
                    ),
                    f"Счет в личном кабинете: {self._absolute_url(invoice_path)}",
                ]
            ),
            [invoice.client_email],
            {"invoice_id": invoice.pk, "payment_id": payment.pk},
        )

    def notify_duplicate_payment(self, payment):
        invoice = payment.invoice
        if not self.enabled:
            return False
        return self.send(
            f"Требуется проверка повторной оплаты счета #{invoice.pk}",
            "\n".join(
                [
                    "Robokassa подтвердила еще один платеж по уже оплаченному счету.",
                    "",
                    f"Счет: {invoice.title}",
                    f"Повторный платеж: #{payment.pk}",
                    f"Сумма: {payment.amount} ₽",
                    "Проверьте операцию и при необходимости оформите возврат.",
                    f"Счет в Office: {self._absolute_url(reverse('office:invoice-detail', kwargs={'pk': invoice.pk}))}",
                ]
            ),
            self.admin_emails,
            {"invoice_id": invoice.pk, "payment_id": payment.pk, "duplicate": True},
        )

    def notify_payment_review(self, payment, reason):
        invoice = payment.invoice
        if not self.enabled:
            return False
        return self.send(
            f"Требуется проверка платежа #{payment.pk}",
            "\n".join(
                [
                    "Платеж Robokassa нельзя обработать автоматически.",
                    "",
                    f"Счет: #{invoice.pk} — {invoice.title}",
                    f"Платеж: #{payment.pk}",
                    f"Сумма: {payment.amount} ₽",
                    f"Причина: {reason}",
                    "Проверьте операцию в кабинете Robokassa и в AurumWeb.",
                    f"Счет в Office: {self._absolute_url(reverse('office:invoice-detail', kwargs={'pk': invoice.pk}))}",
                ]
            ),
            self.admin_emails,
            {"invoice_id": invoice.pk, "payment_id": payment.pk, "review": True},
        )

    def notify_system_alert(self, subject, message, payload=None):
        if not self.enabled:
            return False
        return self.send(subject, message, self.admin_emails, payload or {})

    def notify_deadline_reminder(self, payload):
        if not (self.enabled and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED):
            return False
        days_left = payload["days_left"]
        deadline = payload.get("deadline_display") or payload["deadline"]
        path = payload.get("path") or "/client/"
        return self.send(
            f"Напоминание о сроке: {payload['title']}",
            "\n".join(
                [
                    payload["title"],
                    "",
                    f"Дата: {deadline}",
                    f"Осталось дней: {days_left}",
                    f"Открыть в личном кабинете: {self._absolute_url(path)}",
                ]
            ),
            [payload.get("email")],
            {"kind": "deadline_reminder", **payload},
        )
