from django import forms

from content.models import Service

from .models import Lead


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ('name', 'email', 'service', 'message')
        widgets = {
            'message': forms.Textarea(attrs={'rows': 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['service'].queryset = Service.objects.filter(is_published=True)
        self.fields['service'].required = False
        self.fields['service'].empty_label = 'Выберите услугу'
