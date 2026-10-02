from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateProjectStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=settings.AURUMWEB_PRIVATE_MEDIA_ROOT, base_url=None)


private_project_storage = PrivateProjectStorage()
