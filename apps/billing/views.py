from decimal import Decimal, InvalidOperation

from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from apps.core.rate_limit import rate_limited
from apps.integrations.robokassa import RobokassaClient

from .models import Invoice, Payment
from .services import mark_payment_succeeded, payment_url_for_invoice


def _noindex(response):
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def _robokassa_payload(request):
    data = request.POST or request.GET
    return data, data.get("OutSum"), data.get("InvId"), data.get("SignatureValue")


def _payment_by_inv_id(inv_id):
    if not inv_id:
        return None
    try:
        return Payment.objects.select_related("invoice").get(pk=inv_id)
    except (Payment.DoesNotExist, ValueError):
        return None


def _payload_dict(data):
    if hasattr(data, "dict"):
        return data.dict()
    return dict(data)


def invoice_detail(request, token):
    invoice = get_object_or_404(Invoice, public_token=token)
    if not invoice.public_access_available:
        return _noindex(HttpResponse("Срок действия ссылки на счет истек.", status=410))
    return _noindex(render(request, "billing/invoice_detail.html", {"invoice": invoice}))


def pay_invoice(request, token):
    invoice = get_object_or_404(Invoice, public_token=token)
    if not invoice.public_access_available:
        return _noindex(HttpResponse("Срок действия ссылки на счет истек.", status=410))
    if invoice.status != Invoice.Status.ISSUED:
        return redirect("billing:invoice-detail", token=invoice.public_token)

    client = RobokassaClient()
    if not client.configured:
        return _noindex(render(request, "billing/payment_preview.html", {"invoice": invoice}))
    return redirect(payment_url_for_invoice(invoice))


@csrf_exempt
def robokassa_result(request):
    client = RobokassaClient()
    if not client.configured or rate_limited(request, "robokassa-result", limit=30, window=60):
        return HttpResponseBadRequest("Invalid Robokassa request")

    data, out_sum, inv_id, signature = _robokassa_payload(request)
    if not out_sum or not inv_id or not signature:
        return HttpResponseBadRequest("Invalid Robokassa request")

    try:
        payment = Payment.objects.select_related("invoice").get(pk=inv_id)
        Decimal(str(out_sum))
    except (Payment.DoesNotExist, InvalidOperation, ValueError):
        return HttpResponseBadRequest("Invalid Robokassa request")

    signature_valid = client.verify_result(out_sum, inv_id, signature)
    payload = _payload_dict(data)
    if not signature_valid:
        return HttpResponseBadRequest("Invalid Robokassa request")

    if not client.amount_matches(payment.amount, out_sum):
        return HttpResponseBadRequest("Invalid Robokassa request")

    mark_payment_succeeded(payment, payload, signature_valid=True)
    return HttpResponse(f"OK{inv_id}", content_type="text/plain")


def robokassa_success(request):
    _data, out_sum, inv_id, signature = _robokassa_payload(request)
    candidate = _payment_by_inv_id(inv_id)
    payment = None
    client = RobokassaClient()
    signature_valid = None
    amount_valid = None
    status_message = (
        "Платежная форма вернула клиента на сайт. Окончательный статус выставляется по ResultURL от Robokassa."
    )

    if candidate and out_sum and signature and client.configured:
        try:
            Decimal(str(out_sum))
            signature_valid = client.verify_success(out_sum, inv_id, signature)
            amount_valid = client.amount_matches(candidate.amount, out_sum)
            if signature_valid and amount_valid:
                payment = candidate
                if payment.status == Payment.Status.SUCCEEDED:
                    status_message = "Платеж подтвержден Robokassa, счет отмечен как оплаченный."
                elif payment.status == Payment.Status.DUPLICATE:
                    status_message = "Платеж подтвержден. Менеджер уже получил уведомление и проверит зачисление."
                else:
                    status_message = (
                        "Подпись SuccessURL корректна. Ждем серверное подтверждение ResultURL от Robokassa."
                    )
        except InvalidOperation:
            signature_valid = False
            amount_valid = False

    return _noindex(
        render(
            request,
            "billing/payment_success.html",
            {
                "payment": payment,
                "invoice": payment.invoice if payment else None,
                "signature_valid": signature_valid,
                "amount_valid": amount_valid,
                "status_message": status_message,
            },
        )
    )


def robokassa_fail(request):
    return _noindex(render(request, "billing/payment_fail.html", {"payment": None, "invoice": None}))
