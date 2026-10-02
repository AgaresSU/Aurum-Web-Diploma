from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.robokassa_result, name="robokassa-result-root"),
    path("invoices/<uuid:token>/", views.invoice_detail, name="invoice-detail"),
    path("invoices/<uuid:token>/pay/", views.pay_invoice, name="pay-invoice"),
    path("robokassa/result/", views.robokassa_result, name="robokassa-result"),
    path("robokassa/success/", views.robokassa_success, name="robokassa-success"),
    path("robokassa/fail/", views.robokassa_fail, name="robokassa-fail"),
]
