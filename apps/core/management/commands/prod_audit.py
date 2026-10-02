import json

from django.core.management.base import BaseCommand, CommandError

from apps.core.prod_readiness import build_readiness, readiness_summary


class Command(BaseCommand):
    help = "Run AurumWeb production readiness audit."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Return a non-zero exit code when warnings are present, not only blockers.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON instead of text.",
        )
        parser.add_argument(
            "--no-fail",
            action="store_true",
            help="Always return zero exit code; useful for local dashboards and reports.",
        )

    def handle(self, *args, **options):
        groups = build_readiness()
        summary = readiness_summary(groups)

        if options["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "summary": {
                            key: value
                            for key, value in summary.items()
                            if key not in {"warning_items", "blocker_items"}
                        },
                        "groups": groups,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            self.stdout.write("AurumWeb production audit")
            self.stdout.write(
                f"Score: {summary['score']}% | OK: {summary['ok']} | "
                f"Warnings: {summary['warnings']} | Blockers: {summary['blockers']} | Total: {summary['total']}"
            )
            for group in groups:
                self.stdout.write("")
                self.stdout.write(f"[{group['title']}]")
                for item in group["items"]:
                    marker = {"ok": "OK", "warning": "WARN", "error": "FAIL"}.get(item["state"], item["state"].upper())
                    self.stdout.write(f"{marker}: {item['title']} — {item['text']}")

        if options["no_fail"]:
            return
        if summary["blockers"]:
            blocker_titles = ", ".join(item["title"] for item in summary["blocker_items"])
            raise CommandError(f"Production audit has blockers: {summary['blockers']}: {blocker_titles}.")
        if options["strict"] and summary["warnings"]:
            raise CommandError(f"Production audit has warnings: {summary['warnings']}.")
