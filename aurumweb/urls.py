from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve

from content import views as content_views
from core import views as core_views
from leads import views as lead_views


urlpatterns = [
    path('', core_views.home, name='home'),
    path('legal/<slug:slug>/', core_views.missing_page, name='legal-page'),
    path(
        'favicon.ico',
        RedirectView.as_view(url=f'{settings.STATIC_URL}favicon.ico', permanent=True),
        name='favicon',
    ),
    path('brief/', lead_views.public_brief, name='public-brief'),
    path('template-demos/<slug:slug>/', content_views.template_site_demo, name='template-site-demo'),
    path('admin/', admin.site.urls),
    path('content/', include('content.urls')),
    path('accounts/', include('accounts.urls')),
]

if settings.DEBUG:
    urlpatterns += [
        re_path(
            r'^assets/(?P<path>.*)$',
            serve,
            {'document_root': settings.BASE_DIR / 'website' / 'static'},
        ),
    ]

handler404 = core_views.page_not_found
