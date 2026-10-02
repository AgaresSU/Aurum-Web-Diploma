from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve

from apps.accounts import views as account_views
from apps.accounts.access import can_use_django_admin
from apps.billing import views as billing_views
from apps.content import views as content_views
from apps.core import views as core_views
from apps.leads import views as lead_views

admin.site.site_header = "AurumWeb Admin"
admin.site.site_title = "AurumWeb Admin"
admin.site.index_title = "Системная админка"
admin.site.has_permission = lambda request: can_use_django_admin(request.user)

urlpatterns = [
    path("", core_views.home, name="home"),
    path("index.html", core_views.index, name="prototype-index"),
    path("concepts/<slug:name>.html", core_views.concept, name="concept"),
    path("legal/<slug:slug>/", core_views.legal_page, name="legal-page"),
    path("fast/legal/<slug:slug>/", core_views.fast_legal_page, name="fast-legal-page"),
    path(
        "favicon.ico",
        RedirectView.as_view(url=f"{settings.STATIC_URL}favicon.ico", permanent=True),
        name="favicon",
    ),
    path("robots.txt", core_views.robots_txt, name="robots"),
    path("sitemap.xml", core_views.sitemap_xml, name="sitemap"),
    path("health/live/", core_views.health_live, name="health-live"),
    path("health/ready/", core_views.health_ready, name="health-ready"),
    path("healthz/", core_views.healthz, name="healthz"),
    path("brief/", lead_views.public_brief, name="public-brief"),
    path(
        "template-demos/<slug:slug>/assets/<path:path>", content_views.template_site_asset, name="template-site-asset"
    ),
    path("template-demos/<slug:slug>/", content_views.template_site_demo, name="template-site-demo"),
    path("admin/login/", account_views.admin_login_redirect, name="admin-login-redirect"),
    path("admin/", admin.site.urls),
    path("office/", include("apps.office.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("client/", include("apps.client_portal.urls")),
    path("projects/", include("apps.projects.urls")),
    path("content/", include("apps.content.urls")),
    path("api/leads/", include("apps.leads.urls")),
    path("messenger/", include("apps.messaging.urls")),
    path("billing", billing_views.robokassa_result, name="robokassa-result-short"),
    path("billing/", include("apps.billing.urls")),
    path("integrations/", include("apps.integrations.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        re_path(r"^assets/(?P<path>.*)$", serve, {"document_root": settings.WEBSITE_STATIC_DIR}),
        re_path(r"^screenshots/(?P<path>.*)$", serve, {"document_root": settings.BASE_DIR / "screenshots"}),
        re_path(r"^template-demos/(?P<path>.+)$", serve, {"document_root": settings.WEBSITE_DEMO_SITES_DIR}),
    ]
    urlpatterns += static(settings.STATIC_URL, document_root=settings.WEBSITE_STATIC_DIR)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = core_views.page_not_found
handler500 = core_views.server_error
