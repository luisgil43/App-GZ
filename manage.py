import os
import sys

# Solo cargar dotenv si estamos en desarrollo
if os.environ.get("DJANGO_DEVELOPMENT") == "true":
    try:
        import dotenv
        dotenv.load_dotenv()
    except ImportError:
        print("⚠️ 'python-dotenv' no está instalado, ignorando .env")

if __name__ == '__main__':
    os.environ.setdefault(
        'DJANGO_SETTINGS_MODULE',
        os.getenv('DJANGO_SETTINGS_MODULE', 'gz_services.settings.dev')
    )
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)
