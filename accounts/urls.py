from django.urls import path

from core.views import missing_page


app_name = 'accounts'

urlpatterns = [
    path('login/', missing_page, name='login'),
]
