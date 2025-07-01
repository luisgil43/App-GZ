import os
import dotenv

from django.core.wsgi import get_wsgi_application

dotenv.load_dotenv()

os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.getenv(
    'DJANGO_SETTINGS_MODULE', 'gz_services.settings.prod'))

application = get_wsgi_application()
