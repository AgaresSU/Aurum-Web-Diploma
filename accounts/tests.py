from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile


class AccountTests(TestCase):
    def test_registration_creates_profile_and_logs_user_in(self):
        response = self.client.post(
            reverse('accounts:register'),
            {
                'username': 'newuser',
                'email': 'new@example.com',
                'password1': 'Strong-pass-2026',
                'password2': 'Strong-pass-2026',
            },
        )
        self.assertRedirects(response, reverse('accounts:account'))
        user = User.objects.get(username='newuser')
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_account_requires_login(self):
        response = self.client.get(reverse('accounts:account'))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('accounts:account')}")
