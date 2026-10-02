from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a timestamped ZIP archive of public and private uploaded files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=str(settings.AURUMWEB_BACKUP_DIR),
            help="Directory for backup files. Defaults to AURUMWEB_BACKUP_DIR.",
        )
        parser.add_argument(
            "--allow-empty",
            action="store_true",
            help="Create an empty archive when uploaded file directories have no files.",
        )

    def handle(self, *args, **options):
        roots = (
            ("media", Path(settings.MEDIA_ROOT)),
            ("private_media", Path(settings.AURUMWEB_PRIVATE_MEDIA_ROOT)),
        )
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        files = []
        for label, root in roots:
            if not root.exists() and options["allow_empty"]:
                root.mkdir(parents=True, exist_ok=True)
            if root.exists():
                files.extend((label, root, path) for path in root.rglob("*") if path.is_file())
        if not files and not options["allow_empty"]:
            self.stdout.write(self.style.WARNING("No uploaded files found."))
            return

        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        archive_path = output_dir / f"media-{timestamp}.zip"
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            for label, root, path in files:
                archive.write(path, Path(label) / path.relative_to(root))

        self.stdout.write(self.style.SUCCESS(f"Media backup created: {archive_path}"))
