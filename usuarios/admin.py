# usuarios/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Rol
from .forms import CustomUserCreationForm


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    add_form = CustomUserCreationForm  # <-- clave

    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')
    filter_horizontal = ('roles', 'groups', 'user_permissions')

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Información personal', {
         'fields': ('first_name', 'last_name', 'email', 'identidad')}),
        ('Roles y permisos', {'fields': (
            'roles', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas importantes', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'identidad', 'roles', 'password1', 'password2'),
        }),
    )


admin.site.register(Rol)
