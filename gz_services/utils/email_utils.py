import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ⚠️ Requeridos en PROD (no hardcodear):
EMAIL_USER = os.environ["EMAIL_HOST_USER"]
EMAIL_PASS = os.environ["EMAIL_HOST_PASSWORD"]

# Opcionales con default:
EMAIL_HOST = os.environ.get("EMAIL_HOST", "mail.grupogzs.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465"))  # SSL


def enviar_correo_manual(destinatario, asunto, cuerpo_texto, cuerpo_html=None):
    mensaje = MIMEMultipart('alternative')
    mensaje['Subject'] = asunto
    mensaje['From'] = EMAIL_USER
    mensaje['To'] = destinatario

    mensaje.attach(MIMEText(cuerpo_texto, 'plain'))
    if cuerpo_html:
        mensaje.attach(MIMEText(cuerpo_html, 'html'))

    try:
        # Timeout para evitar cuelgues si el servidor no responde
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, timeout=30) as servidor:
            # Mensaje claro si credenciales no son válidas
            servidor.login(EMAIL_USER, EMAIL_PASS)
            servidor.sendmail(EMAIL_USER, [destinatario], mensaje.as_string())
        print(f"✅ Correo enviado correctamente a {destinatario}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("❌ Error de autenticación SMTP: revisa EMAIL_HOST_USER/EMAIL_HOST_PASSWORD.")
        return False

    except Exception as e:
        print("❌ Error al enviar el correo:", e)
        return False
