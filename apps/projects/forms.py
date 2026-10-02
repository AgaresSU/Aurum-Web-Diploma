from django import forms
from django.conf import settings
from django.db.models import Sum

from .models import Project, ProjectFile, ProjectStage


class ProjectUpdateForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = (
            "title",
            "service_type",
            "user",
            "manager",
            "client_name",
            "client_email",
            "status",
            "summary",
            "next_action",
            "starts_at",
            "due_at",
        )
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 4}),
            "starts_at": forms.DateInput(attrs={"type": "date"}),
            "due_at": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "title": "Название проекта",
            "service_type": "Направление",
            "user": "Клиент",
            "manager": "Ответственный",
            "client_name": "Имя клиента",
            "client_email": "Email клиента",
            "status": "Статус",
            "summary": "Краткое описание",
            "next_action": "Следующий шаг для клиента",
            "starts_at": "Начало работ",
            "due_at": "Плановый срок",
        }


class ProjectStageForm(forms.ModelForm):
    class Meta:
        model = ProjectStage
        fields = (
            "title",
            "description",
            "result_summary",
            "result_url",
            "status",
            "sort_order",
            "due_at",
            "approval_required",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "result_summary": forms.Textarea(attrs={"rows": 4}),
            "due_at": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "title": "Название этапа",
            "description": "Описание этапа",
            "result_summary": "Что выполнено",
            "result_url": "Ссылка на результат",
            "status": "Статус",
            "sort_order": "Порядок",
            "due_at": "Срок",
            "approval_required": "Нужно согласование клиента",
        }


class ProjectFileUploadForm(forms.ModelForm):
    class Meta:
        model = ProjectFile
        fields = ("title", "file", "stage", "client_visible")
        labels = {
            "title": "Название",
            "file": "Файл",
            "stage": "Связать с этапом",
            "client_visible": "Показывать клиенту",
        }

    def __init__(self, *args, project=None, allow_visibility=True, allow_stage=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["file"].widget.attrs[
            "accept"
        ] = ".pdf,.doc,.docx,.xls,.xlsx,.ods,.odt,.txt,.csv,.rtf,.png,.jpg,.jpeg,.webp,.svg,.zip,.rar,.7z"
        if not allow_visibility:
            self.fields.pop("client_visible")
        if allow_stage:
            self.fields["stage"].queryset = project.stages.all() if project else ProjectStage.objects.none()
            self.fields["stage"].required = False
            self.fields["stage"].empty_label = "Без привязки к этапу"
        else:
            self.fields.pop("stage")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if not self.project:
            return uploaded
        file_count = self.project.files.count()
        total_bytes = self.project.files.aggregate(total=Sum("size"))["total"] or 0
        max_files = max(int(getattr(settings, "AURUMWEB_PROJECT_UPLOAD_MAX_FILES", 100)), 1)
        max_bytes = max(int(getattr(settings, "AURUMWEB_PROJECT_UPLOAD_MAX_BYTES", 250 * 1024 * 1024)), 1)
        if file_count >= max_files:
            raise forms.ValidationError(
                "Достигнут лимит файлов проекта. Удалите ненужные файлы или обратитесь к менеджеру."
            )
        if total_bytes + uploaded.size > max_bytes:
            raise forms.ValidationError("Достигнут общий лимит хранилища проекта. Обратитесь к менеджеру.")
        return uploaded

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded = self.cleaned_data["file"]
        instance.original_name = uploaded.name
        instance.size = uploaded.size
        if "client_visible" not in self.fields:
            instance.client_visible = True
        if commit:
            instance.save()
        return instance
