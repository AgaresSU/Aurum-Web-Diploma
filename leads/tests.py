from django.test import TestCase
from django.urls import reverse

from content.models import Service, Work

from .models import Lead


class LeadFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.service = Service.objects.create(
            title='Тестовая услуга',
            slug='test-service',
            short_description='Описание',
        )
        cls.work = Work.objects.create(
            title='Тестовая работа',
            slug='lead-test-work',
            category='Лендинг',
            short_description='Описание',
        )

    def test_valid_form_creates_lead(self):
        response = self.client.post(
            reverse('brief'),
            {
                'name': 'Иван',
                'email': 'ivan@example.com',
                'service': self.service.pk,
                'message': 'Нужен небольшой сайт.',
                'project': self.work.slug,
            },
        )
        self.assertRedirects(response, reverse('brief_success'))
        lead = Lead.objects.get(email='ivan@example.com')
        self.assertEqual(lead.service, self.service)
        self.assertEqual(lead.work, self.work)
