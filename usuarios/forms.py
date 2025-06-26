# usuarios/forms.py
from django import forms
from usuarios.models import CustomUser, Rol
from django.contrib.auth.forms import UserCreationForm


class UsuarioForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Rol.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'space-y-1'}),
        required=False
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'first_name', 'last_name', 'identidad',
                  'email', 'is_active', 'is_staff', 'is_superuser', 'roles']


class CustomUserCreationForm(UserCreationForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Rol.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'identidad',
                  'roles', 'password1', 'password2')
