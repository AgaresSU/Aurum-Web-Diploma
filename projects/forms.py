from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ('title', 'service', 'status', 'summary', 'next_action', 'starts_at', 'due_at')
        widgets = {
            'summary': forms.Textarea(attrs={'rows': 5}),
            'starts_at': forms.DateInput(attrs={'type': 'date'}),
            'due_at': forms.DateInput(attrs={'type': 'date'}),
        }
