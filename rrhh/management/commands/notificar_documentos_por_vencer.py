from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from rrhh.models import DocumentoTrabajador
from usuarios.models import CustomUser
from usuarios.utils import crear_notificacion


def enviar_notificaciones_documentos_vencidos():
    hoy = timezone.now().date()
    dias_alerta = [20, 15, 10, 5, 2, 0]

    documentos = DocumentoTrabajador.objects.filter(
        fecha_vencimiento__isnull=False
    )

    for doc in documentos:
        dias_restantes = (doc.fecha_vencimiento - hoy).days

        if dias_restantes in dias_alerta or dias_restantes < 0:
            trabajador = doc.trabajador
            url_trabajador = reverse('rrhh:mis_documentos')
            url_admin = reverse('rrhh:listado_documentos')

            # Mensaje base
            if dias_restantes > 0:
                mensaje = f"vence en {dias_restantes} días."
            elif dias_restantes == 0:
                mensaje = f"vence hoy."
            else:
                mensaje = f"está vencido desde el {doc.fecha_vencimiento.strftime('%d-%m-%Y')}."

            # 1. Notificar al trabajador
            crear_notificacion(
                usuario=trabajador,
                mensaje=f"Tu documento '{doc.tipo_documento}' {mensaje}",
                url=url_trabajador
            )

            # 2. Notificar al PM directo si tiene
            if trabajador.pm:
                crear_notificacion(
                    usuario=trabajador.pm,
                    mensaje=f"El documento '{doc.tipo_documento}' de {trabajador.get_full_name()} {mensaje}",
                    url=url_admin
                )

            # 3. Notificar a otros PM (evitando duplicado)
            pms = CustomUser.objects.filter(roles__nombre='pm').exclude(
                id=getattr(trabajador.pm, 'id', None)).distinct()
            for pm in pms:
                crear_notificacion(
                    usuario=pm,
                    mensaje=f"El documento '{doc.tipo_documento}' de {trabajador.get_full_name()} {mensaje}",
                    url=url_admin
                )

            # 4. Notificar a todos los usuarios RRHH
            rrhhs = CustomUser.objects.filter(roles__nombre='rrhh').distinct()
            for rrhh in rrhhs:
                crear_notificacion(
                    usuario=rrhh,
                    mensaje=f"El documento '{doc.tipo_documento}' de {trabajador.get_full_name()} {mensaje}",
                    url=url_admin
                )
