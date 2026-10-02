import json
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.content.frombiz_raw_templates import copy_raw_frombiz_template, has_raw_frombiz_template
from apps.content.models import TemplateProduct


class Command(BaseCommand):
    help = "Build standalone production-ready static site template folders."

    def handle(self, *args, **options):
        output_dir = settings.WEBSITE_DEMO_SITES_DIR
        output_dir.mkdir(exist_ok=True)
        catalog = []

        templates = TemplateProduct.objects.filter(
            is_published=True,
            template_type=TemplateProduct.TemplateType.WEBSITE,
        )
        for template in templates:
            if not has_raw_frombiz_template(template.slug):
                self.stderr.write(self.style.WARNING(f"Skipped {template.slug}: source template is unavailable."))
                continue
            copy_raw_frombiz_template(template, output_dir)
            catalog.append(self._catalog_entry(template))

        (output_dir / "_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        self._clean_stale_demo_dirs(output_dir, {entry["slug"] for entry in catalog})
        self.stdout.write(self.style.SUCCESS(f"Built {len(catalog)} standalone site templates in {output_dir}"))

    def _catalog_entry(self, template):
        return {
            "slug": template.slug,
            "title": template.title,
            "category": template.category,
            "industry": template.industry,
            "short_description": template.short_description,
            "conversion_focus": template.conversion_focus,
            "demo_url": f"/template-demos/{template.slug}/",
            "preview_desktop_webp": f"template-previews/desktop-webp/{template.slug}.webp",
            "preview_mobile_webp": f"template-previews/mobile-webp/{template.slug}.webp",
        }

    def _clean_stale_demo_dirs(self, output_dir, active_slugs):
        for path in output_dir.iterdir():
            if path.name == "_catalog.json":
                continue
            if path.is_dir() and path.name not in active_slugs:
                shutil.rmtree(path)
