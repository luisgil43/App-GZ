from django.utils import timezone
from usuarios.models import FirmaRepresentanteLegal  # 👈 importa el modelo
import base64
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ObjectDoesNotExist
# Asegúrate de que esta importación sea correcta
from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required


class UsuarioLoginView(LoginView):
    template_name = 'dashboard/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated:
            return reverse_lazy('dashboard:inicio')
        logout(self.request)
        return reverse_lazy('usuarios:login')


class AdminLoginView(LoginView):
    template_name = 'dashboard_admin/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated and user.is_staff:
            return reverse_lazy('admin:index')
        logout(self.request)
        return reverse_lazy('usuarios:admin_login')


def no_autorizado_view(request):
    return render(request, 'usuarios/no_autorizado.html', status=403)


@staff_member_required
def subir_firma_representante(request):
    if request.method == 'POST':
        data_url = request.POST.get('firma_digital')
        if not data_url or not data_url.startswith('data:image/png;base64,'):
            messages.error(request, "Firma inválida o vacía.")
            return redirect(request.path)

        try:
            formato, img_base64 = data_url.split(';base64,')
            data = base64.b64decode(img_base64)
            content = ContentFile(data)
            nombre_archivo = "firma.png"

            # Eliminar firma anterior (incluyendo el archivo en Cloudinary)
            firma_anterior = FirmaRepresentanteLegal.objects.first()
            if firma_anterior:
                if firma_anterior.archivo:
                    firma_anterior.archivo.delete(
                        save=False)  # Elimina de Cloudinary
                firma_anterior.delete()  # Elimina el registro en DB

            # Crear nueva firma
            firma = FirmaRepresentanteLegal(fecha_subida=timezone.now())
            firma.archivo.save(nombre_archivo, content, save=True)

            messages.success(
                request, "Firma del representante legal subida correctamente.")
            return redirect('liquidaciones:admin_lista')

        except Exception as e:
            messages.error(request, f"Error al guardar firma: {e}")
            return redirect(request.path)

    return render(request, 'usuarios/subir_firma_representante.html')
