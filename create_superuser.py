from django.contrib.auth.models import User
import os
import django

# 1. Configurar variable de entorno para settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gz_services.settings')

# 2. Inicializar Django
django.setup()

# 3. Ahora sí importar modelos y usar ORM

username = "admin"
email = "luisggil01@gmail.com"
password = "TuPasswordSeguro123"  # Cambia la contraseña aquí

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(
        username=username, email=email, password=password)
    print("✅ Superusuario creado exitosamente.")
else:
    print("ℹ️ El superusuario ya existe.")
