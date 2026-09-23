from django.test import TestCase
from django.urls import reverse

from .models import Work


class WorkViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.work = Work.objects.create(
            title='Тестовый сайт',
            slug='test-work',
            category='Лендинг',
            short_description='Проверка страницы проекта.',
            is_published=True,
        )

    def test_work_list_opens(self):
        response = self.client.get(reverse('works'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.work.title)

    def test_work_search_filters_list(self):
        response = self.client.get(reverse('works'), {'q': 'Тестовый'})
        self.assertContains(response, self.work.title)

        response = self.client.get(reverse('works'), {'q': 'Не существует'})
        self.assertNotContains(response, self.work.title)

    def test_work_detail_opens(self):
        response = self.client.get(reverse('work_detail', args=[self.work.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.work.title)
