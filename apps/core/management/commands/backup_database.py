import os
import sqlite3
import subprocess
from pathlib import Path
from shutil import which

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a timestamped database backup and JSON data export."

    def add_arguments(self, parser):
        default_backup_dir = os.getenv(
            "AURUMWEB_BACKUP_DIR",
            str(getattr(settings, "AURUMWEB_BACKUP_DIR", settings.BASE_DIR / "backups")),
        )
        parser.add_argument(
            "--output-dir",
            default=default_backup_dir,
            help="Directory for backup files. Defaults to AURUMWEB_BACKUP_DIR.",
        )
        parser.add_argument(
            "--skip-json",
            action="store_true",
            help="Skip dumpdata JSON export.",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        artifacts = []

        db_config = settings.DATABASES["default"]
        if db_config["ENGINE"] == "django.db.backends.sqlite3" and db_config["NAME"] != ":memory:":
            source = Path(db_config["NAME"])
            if source.exists():
                sqlite_backup = output_dir / f"sqlite-{timestamp}.sqlite3"
                with sqlite3.connect(source) as source_connection, sqlite3.connect(sqlite_backup) as backup_connection:
                    source_connection.backup(backup_connection)
                artifacts.append(sqlite_backup)
            else:
                self.stdout.write(self.style.WARNING(f"SQLite database not found: {source}"))
        elif "postgresql" in db_config["ENGINE"]:
            pg_dump = which("pg_dump")
            if pg_dump:
                postgres_backup = output_dir / f"postgres-{timestamp}.dump"
                env = os.environ.copy()
                if db_config.get("PASSWORD"):
                    env["PGPASSWORD"] = str(db_config["PASSWORD"])
                command = [
                    pg_dump,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--file",
                    str(postgres_backup),
                ]
                if db_config.get("HOST"):
                    command.extend(["--host", str(db_config["HOST"])])
                if db_config.get("PORT"):
                    command.extend(["--port", str(db_config["PORT"])])
                if db_config.get("USER"):
                    command.extend(["--username", str(db_config["USER"])])
                command.append(str(db_config["NAME"]))
                try:
                    subprocess.run(command, check=True, env=env, capture_output=True, text=True)
                except subprocess.CalledProcessError as exc:
                    self.stdout.write(self.style.WARNING(f"pg_dump failed: {exc.stderr.strip() or exc}"))
                else:
                    artifacts.append(postgres_backup)
            else:
                self.stdout.write(self.style.WARNING("pg_dump not found. JSON export will still be created."))

        if not options["skip_json"]:
            json_backup = output_dir / f"data-{timestamp}.json"
            with json_backup.open("w", encoding="utf-8") as stream:
                call_command(
                    "dumpdata",
                    natural_foreign=True,
                    natural_primary=True,
                    indent=2,
                    exclude=["contenttypes", "auth.permission"],
                    stdout=stream,
                )
            artifacts.append(json_backup)

        if not artifacts:
            self.stdout.write(self.style.WARNING("No backup artifacts were created."))
            return

        self.stdout.write(self.style.SUCCESS("Backup created:"))
        for artifact in artifacts:
            self.stdout.write(f"  {artifact}")
