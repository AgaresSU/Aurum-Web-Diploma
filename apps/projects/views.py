from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from .models import ProjectFile


def _can_access_file(user, project_file):
    if user.is_staff or user.is_superuser:
        return True
    project = project_file.project
    if not project_file.client_visible:
        return False
    if project.user_id == user.pk:
        return True
    return bool(user.email and project.client_email and user.email.lower() == project.client_email.lower())


@login_required
def download_project_file(request, token):
    project_file = get_object_or_404(ProjectFile.objects.select_related("project"), public_id=token)
    if not _can_access_file(request.user, project_file):
        raise Http404
    try:
        handle = project_file.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    return FileResponse(handle, as_attachment=True, filename=project_file.original_name)
