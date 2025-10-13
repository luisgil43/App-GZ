# dashboard_admin/forms.py

from django import forms
from usuarios.models import CustomUser


class UsuarioForm(forms.ModelForm):
    password = forms.CharField(
        label='Contraseña',
        widget=forms.PasswordInput,
        required=False,
        help_text="Dejar en blanco para no cambiar la contraseña."
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'first_name', 'last_name', 'email', 'identidad',
                  'is_active', 'is_staff', 'is_superuser', 'rol']
        widgets = {
            'rol': forms.Select(attrs={'class': 'input input-bordered w-full'}),
        }
        labels = {
            'first_name': 'Nombre',
            'last_name': 'Apellidos',
            'email': 'Correo Electrónico',
            'rol': 'Rol del usuario',
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')

        if password:
            user.set_password(password)

        if commit:
            user.save()
        return user
