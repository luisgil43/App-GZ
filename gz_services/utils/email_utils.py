import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

EMAIL_USER = 'planix@grupogzs.com'
EMAIL_PASS = '}xZs%l%xGFb3'


def enviar_correo_manual(destinatario, asunto, cuerpo_texto, cuerpo_html=None):
    mensaje = MIMEMultipart('alternative')
    mensaje['Subject'] = asunto
    mensaje['From'] = EMAIL_USER
    mensaje['To'] = destinatario

    mensaje.attach(MIMEText(cuerpo_texto, 'plain'))
    if cuerpo_html:
        mensaje.attach(MIMEText(cuerpo_html, 'html'))

    try:
        servidor = smtplib.SMTP_SSL('mail.grupogzs.com', 465)
        servidor.login(EMAIL_USER, EMAIL_PASS)
        servidor.sendmail(EMAIL_USER, [destinatario], mensaje.as_string())
        servidor.quit()
        print(f"✅ Correo enviado correctamente a {destinatario}")
        return True
    except Exception as e:
        print("❌ Error al enviar el correo:", e)
        return False
