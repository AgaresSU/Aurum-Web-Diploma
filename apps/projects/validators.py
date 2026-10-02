from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

ALLOWED_PROJECT_FILE_EXTENSIONS = (
    "7z",
    "csv",
    "doc",
    "docx",
    "jpeg",
    "jpg",
    "ods",
    "odt",
    "pdf",
    "png",
    "rar",
    "rtf",
    "svg",
    "txt",
    "webp",
    "xls",
    "xlsx",
    "zip",
)
MAX_PROJECT_FILE_SIZE = 15 * 1024 * 1024

validate_project_file_extension = FileExtensionValidator(ALLOWED_PROJECT_FILE_EXTENSIONS)


def validate_project_file_size(value):
    if value.size > MAX_PROJECT_FILE_SIZE:
        raise ValidationError("Размер файла не должен превышать 15 МБ.")


def project_file_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    return f"projects/{instance.project.public_id}/{uuid4().hex}{suffix}"
