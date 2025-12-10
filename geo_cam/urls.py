from django.urls import path

from . import views

app_name = "geo_cam"

urlpatterns = [
    
    path("capture/", views.capture, name="capture"),
    path("upload/", views.upload, name="upload"),
    path("gallery/", views.gallery, name="gallery"),
    path("geocode/", views.geocode_google, name="geocode"),
    path("static-map/", views.static_map, name="static_map"),  # 👈 NUEVO
    
]

