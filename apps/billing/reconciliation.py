import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.models import IntegrationEvent
from apps.integrations.robokassa import RobokassaClient

from .models import Payment
from .services import mark_payment_for_review, mark_payment_succeeded

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationSummary:
    checked: int = 0
    succeeded: int = 0
    duplicate: int = 0
    pending: int = 0
    cancelled: int = 0
    failed: int = 0
    review: int = 0
    errors: int = 0
    skipped: int = 0

    def as_dict(self):
        return self.__dict__.copy()


def _event(payment, status, title, **extra):
    IntegrationEvent.objects.create(
        provider=IntegrationEvent.Provider.ROBOKASSA,
        status=status,
        title=title,
        payload={"payment_id": payment.pk, "invoice_id": payment.invoice_id, **extra},
    )


def _update_check(payment, *, state_code=None, operation_key="", note="", status=None):
    values = {
        "provider_state_code": state_code,
        "last_reconciled_at": timezone.now(),
        "reconciliation_note": note,
    }
    if operation_key:
        values["provider_operation_id"] = operation_key
    if status:
        values["status"] = status
    Payment.objects.filter(pk=payment.pk).update(**values)


def _record_only_when_changed(payment, status, title, note, **extra):
    changed = payment.last_reconciled_at is None or payment.reconciliation_note != note
    if changed:
        _event(payment, status, title, **extra)


def reconcile_payment(payment, *, client=None, dry_run=False):
    client = client or RobokassaClient()
    payment.refresh_from_db()
    if payment.provider != Payment.Provider.ROBOKASSA or payment.status != Payment.Status.PENDING:
        return "skipped"

    if client.config.test_mode:
        note = "Автоматическая сверка OpStateExt недоступна для тестовых платежей Robokassa."
        if not dry_run:
            _record_only_when_changed(
                payment,
                IntegrationEvent.Status.WARNING,
                "Платеж ожидает ручной проверки",
                note,
                reason="robokassa_test_mode",
            )
            _update_check(payment, note=note)
        return "review"

    try:
        state = client.get_operation_state(
            payment.pk,
            timeout=settings.AURUMWEB_ROBOKASSA_STATE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("Robokassa reconciliation failed for payment %s: %s", payment.pk, exc)
        note = f"Не удалось получить состояние операции: {exc}"
        if not dry_run:
            _record_only_when_changed(
                payment,
                IntegrationEvent.Status.ERROR,
                "Ошибка сверки платежа Robokassa",
                note,
                error=str(exc),
            )
            _update_check(payment, note=note)
        return "errors"

    if state.result_code != 0:
        note = state.result_description or f"Robokassa вернула код результата {state.result_code}."
        if not dry_run:
            _record_only_when_changed(
                payment,
                IntegrationEvent.Status.WARNING,
                "Платеж не найден при сверке" if state.result_code == 3 else "Сверка платежа требует внимания",
                note,
                result_code=state.result_code,
            )
            _update_check(payment, note=note)
        return "review"

    payload = {
        "source": "robokassa_op_state_ext",
        "ResultCode": state.result_code,
        "StateCode": state.state_code,
        "OutSum": str(state.out_sum) if state.out_sum is not None else "",
        "StateDate": state.state_date,
        "PaymentMethod": state.payment_method,
    }
    note = f"Состояние Robokassa: {state.state_code}."
    if dry_run:
        if (
            state.state_code == 100
            and state.out_sum is not None
            and client.amount_matches(payment.amount, state.out_sum)
        ):
            if (
                Payment.objects.filter(
                    invoice_id=payment.invoice_id,
                    status=Payment.Status.SUCCEEDED,
                )
                .exclude(pk=payment.pk)
                .exists()
            ):
                return "duplicate"
            return "succeeded"
        if state.state_code == 10:
            return "cancelled"
        if state.state_code == 60:
            return "failed"
        if state.state_code in {80, 100}:
            return "review"
        return "pending"

    if state.state_code == 100:
        if state.out_sum is None or not client.amount_matches(payment.amount, state.out_sum):
            reason = (
                "Robokassa подтверждает оплату, но сумма операции "
                f"({state.out_sum if state.out_sum is not None else 'не указана'}) "
                f"не совпадает с суммой платежа ({payment.amount})."
            )
            payment = mark_payment_for_review(payment, payload, reason, signature_valid=True)
            _update_check(
                payment,
                state_code=state.state_code,
                operation_key=state.operation_key,
                note=reason,
            )
            return "review"
        payment = mark_payment_succeeded(payment, payload, signature_valid=True)
        _update_check(
            payment,
            state_code=state.state_code,
            operation_key=state.operation_key,
            note="Оплата подтверждена сверкой с Robokassa.",
        )
        _event(
            payment,
            IntegrationEvent.Status.SUCCESS,
            "Платеж подтвержден сверкой Robokassa",
            state_code=state.state_code,
        )
        payment.refresh_from_db(fields=("status",))
        if payment.status == Payment.Status.REVIEW:
            return "review"
        if payment.status == Payment.Status.DUPLICATE:
            return "duplicate"
        return "succeeded"

    if state.state_code == 10:
        with transaction.atomic():
            Payment.objects.select_for_update().filter(pk=payment.pk, status=Payment.Status.PENDING).update(
                status=Payment.Status.CANCELLED,
                provider_state_code=state.state_code,
                provider_operation_id=state.operation_key,
                last_reconciled_at=timezone.now(),
                reconciliation_note="Операция отменена на стороне Robokassa.",
            )
        _event(payment, IntegrationEvent.Status.INFO, "Отмененная операция Robokassa сверена", state_code=10)
        return "cancelled"

    if state.state_code == 60:
        with transaction.atomic():
            Payment.objects.select_for_update().filter(pk=payment.pk, status=Payment.Status.PENDING).update(
                status=Payment.Status.FAILED,
                provider_state_code=state.state_code,
                provider_operation_id=state.operation_key,
                last_reconciled_at=timezone.now(),
                reconciliation_note="Robokassa отказала в зачислении и вернула средства покупателю.",
            )
        _event(payment, IntegrationEvent.Status.WARNING, "Robokassa вернула средства покупателю", state_code=60)
        return "failed"

    known_pending_states = {5, 20, 50}
    needs_review = state.state_code == 80 or state.state_code not in known_pending_states
    event_status = IntegrationEvent.Status.WARNING if needs_review else IntegrationEvent.Status.INFO
    event_title = (
        "Платеж приостановлен Robokassa"
        if state.state_code == 80
        else ("Неизвестное состояние платежа Robokassa" if needs_review else "Сверка платежа выполнена")
    )
    _record_only_when_changed(payment, event_status, event_title, note, state_code=state.state_code)
    _update_check(
        payment,
        state_code=state.state_code,
        operation_key=state.operation_key,
        note=note,
    )
    return "review" if needs_review else "pending"


def reconcile_pending_payments(*, older_than_minutes=None, limit=None, payment_id=None, dry_run=False, client=None):
    older_than_minutes = (
        settings.AURUMWEB_ROBOKASSA_RECONCILE_AFTER_MINUTES
        if older_than_minutes is None
        else max(0, older_than_minutes)
    )
    limit = settings.AURUMWEB_ROBOKASSA_RECONCILE_LIMIT if limit is None else max(1, limit)
    cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
    payments = Payment.objects.filter(
        provider=Payment.Provider.ROBOKASSA,
        status=Payment.Status.PENDING,
        created_at__lte=cutoff,
    ).order_by("created_at")
    if payment_id:
        payments = payments.filter(pk=payment_id)

    summary = ReconciliationSummary()
    for payment in payments[:limit]:
        summary.checked += 1
        outcome = reconcile_payment(payment, client=client, dry_run=dry_run)
        setattr(summary, outcome, getattr(summary, outcome) + 1)
    return summary
