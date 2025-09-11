import smtplib
from email.mime.text import MIMEText
import os

EMAIL_USER = os.environ["EMAIL_HOST_USER"]
EMAIL_PASS = os.environ["EMAIL_HOST_PASSWORD"]


def enviar_correo_manual(destinatario, asunto, cuerpo, html=False):
    if html:
        mensaje = MIMEText(cuerpo, 'html')
    else:
        mensaje = MIMEText(cuerpo, 'plain')

    mensaje['Subject'] = asunto
    mensaje['From'] = EMAIL_USER
    mensaje['To'] = destinatario

    try:
        servidor = smtplib.SMTP_SSL('mail.grupogzs.com', 465)
        servidor.login(EMAIL_USER, EMAIL_PASS)
        servidor.sendmail(EMAIL_USER, [destinatario], mensaje.as_string())
        servidor.quit()
        print("✅ Correo enviado correctamente a", destinatario)
        return True
    except Exception as e:
        print("❌ Error al enviar correo:", e)
        return False
