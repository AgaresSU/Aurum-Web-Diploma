from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Project


class ProjectViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='test-pass-2026')
        self.other_user = User.objects.create_user(username='other', password='test-pass-2026')
        self.project = Project.objects.create(owner=self.user, title='Сайт компании')
        self.client.force_login(self.user)

    def test_project_list_contains_only_user_projects(self):
        Project.objects.create(owner=self.other_user, title='Чужой проект')
        response = self.client.get(reverse('projects:list'))
        self.assertContains(response, self.project.title)
        self.assertNotContains(response, 'Чужой проект')

    def test_user_can_create_project(self):
        response = self.client.post(
            reverse('projects:create'),
            {'title': 'Новый проект', 'status': Project.Status.DRAFT},
        )
        project = Project.objects.get(title='Новый проект')
        self.assertEqual(project.owner, self.user)
        self.assertRedirects(response, reverse('projects:detail', args=[project.pk]))

    def test_user_cannot_open_another_users_project(self):
        other_project = Project.objects.create(owner=self.other_user, title='Закрытый проект')
        response = self.client.get(reverse('projects:detail', args=[other_project.pk]))
        self.assertEqual(response.status_code, 404)
