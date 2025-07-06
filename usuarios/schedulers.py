from apscheduler.schedulers.background import BackgroundScheduler
from usuarios.utils import enviar_notificaciones_documentos_vencidos


def iniciar_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        enviar_notificaciones_documentos_vencidos,
        trigger='interval',
        # seconds=30, # ✅ Solo esto durante pruebas
        days=1,
        id='notificaciones_documentos'
    )
    scheduler.start()
