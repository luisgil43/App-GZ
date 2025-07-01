cd gz_services
python - m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

pip install django
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
