from django.core.management.base import BaseCommand

from apps.content.models import TemplateProduct
from apps.content.presentation import client_safe_text
from apps.content.site_template_catalog import SITE_TEMPLATES


class Command(BaseCommand):
    help = "Synchronize the current AurumWeb public website template catalog."

    def handle(self, *args, **options):
        active_slugs = {row["slug"] for row in SITE_TEMPLATES}
        stale_templates = TemplateProduct.objects.filter(template_type=TemplateProduct.TemplateType.WEBSITE).exclude(
            slug__in=active_slugs
        )
        stale_count = stale_templates.count()
        stale_templates.delete()

        for index, row in enumerate(SITE_TEMPLATES, start=1):
            defaults = {
                "title": client_safe_text(row["title"]),
                "template_type": TemplateProduct.TemplateType.WEBSITE,
                "category": client_safe_text(row["category"]),
                "industry": client_safe_text(row["industry"]),
                "short_description": client_safe_text(row["short_description"]),
                "conversion_focus": client_safe_text(row["conversion_focus"]),
                "contents": self._contents(row),
                "implementation_notes": self._implementation_notes(row),
                "is_paid_ready": True,
                "is_published": True,
                "sort_order": 100 + index,
            }
            TemplateProduct.objects.update_or_create(slug=row["slug"], defaults=defaults)

        active_count = TemplateProduct.objects.filter(
            template_type=TemplateProduct.TemplateType.WEBSITE,
            is_published=True,
        ).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Website templates synchronized: {active_count} published, {stale_count} stale removed."
            )
        )

    def _contents(self, row):
        contents = "\n".join(
            [
                f"Позиционирование под нишу: {row['industry']}",
                f"Структура страниц: {row['pages']}",
                f"Конверсия: {row['conversion_focus']}",
                f"Бизнес-интеграции: {row['integrations']}",
                "SEO-база: мета-шаблоны, FAQ-блок, чистая структура посадочных страниц",
                "Админка: редактирование контента, заявок, статусов и будущих счетов",
            ]
        )
        return client_safe_text(contents)

    def _implementation_notes(self, row):
        return client_safe_text(
            "Шаблон рассчитан на быстрый запуск после брифа: меняются тексты, визуальный стиль, "
            "формы, SEO-кластеры и набор интеграций. Можно стартовать как лендинг, а затем развить "
            "в многостраничный сайт с кабинетом клиента, счетами, оплатой и Telegram-уведомлениями."
        )
