from django.urls import path

from . import views

app_name = "geo_cam"

urlpatterns = [
    
    path("capture/", views.capture, name="capture"),    
    path("upload/", views.upload, name="upload"),  
    
]