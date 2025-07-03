import smtplib
from email.mime.text import MIMEText

EMAIL_USER = 'planix@grupogzs.com'
EMAIL_PASS = '}xZs%l%xGFb3'
destinatario = 'luisggil01@gmail.com'

mensaje = MIMEText("Este es un correo enviado manualmente con SMTP_SSL desde Python.")
mensaje['Subject'] = 'Correo SMTP manual'
mensaje['From'] = EMAIL_USER
mensaje['To'] = destinatario

try:
    server = smtplib.SMTP_SSL('mail.grupogzs.com', 465)
    server.login(EMAIL_USER, EMAIL_PASS)
    server.sendmail(EMAIL_USER, [destinatario], mensaje.as_string())
    server.quit()
    print("✅ Correo enviado correctamente.")
except Exception as e:
    print("❌ Error al enviar el correo:", str(e))
