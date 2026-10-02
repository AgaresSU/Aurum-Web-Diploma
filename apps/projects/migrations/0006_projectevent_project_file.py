import django.db.models.deletion
from django.db import migrations, models


def link_existing_file_events(apps, schema_editor):
    ProjectEvent = apps.get_model("projects", "ProjectEvent")
    ProjectFile = apps.get_model("projects", "ProjectFile")

    files_by_project_and_title = {}
    for project_file in ProjectFile.objects.all().iterator():
        key = (project_file.project_id, project_file.title)
        files_by_project_and_title.setdefault(key, []).append(project_file)

    for event in ProjectEvent.objects.filter(kind="file", project_file__isnull=True).iterator():
        prefix = "Добавлен файл: "
        if not event.title.startswith(prefix):
            continue
        title = event.title[len(prefix) :]
        candidates = files_by_project_and_title.get((event.project_id, title), ())
        if not candidates:
            continue
        project_file = min(
            candidates,
            key=lambda item: abs((item.created_at - event.created_at).total_seconds()),
        )
        event.project_file_id = project_file.pk
        event.save(update_fields=("project_file",))


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0005_projectfile_stage_projectstage_result_summary_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectevent",
            name="project_file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="projects.projectfile",
            ),
        ),
        migrations.RunPython(link_existing_file_events, migrations.RunPython.noop),
    ]
