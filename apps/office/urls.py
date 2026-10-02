from django.urls import path

from . import views

app_name = "office"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("inbox/", views.inbox, name="inbox"),
    path("projects/", views.projects, name="projects"),
    path("approvals/", views.approvals, name="approvals"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("projects/<uuid:token>/", views.project_detail, name="project-detail"),
    path("clients/", views.clients, name="clients"),
    path("clients/<int:pk>/", views.client_detail, name="client-detail"),
    path("search/", views.global_search, name="search"),
    path("revenue/", views.revenue, name="revenue"),
    path("security/", views.security, name="security"),
    path("integrations/", views.integrations, name="integrations"),
    path("leads/", views.leads, name="leads"),
    path("leads/<int:pk>/", views.lead_detail, name="lead-detail"),
    path("leads/<int:pk>/invoice/", views.create_invoice, name="create-invoice"),
    path("quick-consultations/", views.quick_consultations, name="quick-consultations"),
    path("quick-consultations/<int:pk>/", views.quick_consultation_detail, name="quick-consultation-detail"),
    path("conversations/", views.conversations, name="conversations"),
    path("conversations/<int:pk>/", views.conversation_detail, name="conversation-detail"),
    path(
        "conversations/<int:pk>/client-preview/", views.conversation_client_preview, name="conversation-client-preview"
    ),
    path("orders/", views.orders, name="orders"),
    path("orders/<int:pk>/", views.order_detail, name="order-detail"),
    path("orders/<int:pk>/site/", views.create_site_for_order, name="create-site-for-order"),
    path("sites/", views.managed_sites, name="sites"),
    path("sites/<int:pk>/", views.managed_site_detail, name="site-detail"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice-detail"),
    path("services/", views.service_prices, name="service-prices"),
    path("services/<int:pk>/", views.service_price_detail, name="service-price-detail"),
]
