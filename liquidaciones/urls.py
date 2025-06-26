from django.urls import path
from . import views
from .views import UsuarioAutocomplete
from .views import verificar_storage


app_name = 'liquidaciones'

urlpatterns = [
    path('', views.listar_liquidaciones, name='listar'),
    path('admin/', views.admin_lista_liquidaciones, name='admin_lista'),
    path('pdf/<int:pk>/', views.ver_pdf_liquidacion, name='ver_pdf'),
    path('registrar-firma/', views.registrar_firma, name='registrar_firma'),
    path('pdfs/', views.liquidaciones_pdf, name='pdfs'),
    path('descargar-pdf/<int:pk>/', views.descargar_pdf, name='descargar_pdf'),
    path('confirmar-firma/<int:pk>/',
         views.confirmar_firma, name='confirmar_firma'),
    path('confirmar-reemplazo/', views.confirmar_reemplazo,
         name='confirmar_reemplazo'),
    path('crear/', views.crear_liquidacion, name='crear'),
    path('usuario-autocomplete/', UsuarioAutocomplete.as_view(),
         name='usuario-autocomplete'),
    path('firmar/<int:pk>/', views.firmar_liquidacion, name='firmar_liquidacion'),
    path('editar/<int:pk>/', views.editar_liquidacion, name='editar'),
    path('eliminar/<int:pk>/', views.eliminar_liquidacion, name='eliminar'),
    path('verificar-storage/', verificar_storage),
    path('ver-firmado/<int:pk>/', views.ver_pdf_firmado_admin,
         name='ver_pdf_firmado_admin'),
    path('ver-pdf/<int:pk>/', views.ver_pdf_admin, name='ver_pdf_admin'),
    path('carga-masiva/', views.carga_masiva_liquidaciones, name='carga_masiva'),




]
