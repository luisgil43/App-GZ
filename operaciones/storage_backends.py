# operaciones/storage_backends.py
import os
from storages.backends.s3boto3 import S3Boto3Storage
from django.conf import settings


class GZWasabiStorage(S3Boto3Storage):
    """
    Storage S3 dedicado para GZ Services (bucket gz-services).
    No toca Cloudinary: úsalo solo en los campos que lo necesiten.
    """
    # ⚠️ No leemos settings aquí a nivel de clase para evitar AttributeError.
    custom_domain = None
    default_acl = "private"
    file_overwrite = True
    querystring_auth = True

    def __init__(self, *args, **kwargs):
        # Leemos de settings SI existe el atributo; si no, de variables de entorno.
        kwargs.setdefault("bucket_name",
                          getattr(settings, "WASABI_GZ_BUCKET_NAME",
                                  os.getenv("WASABI_GZ_BUCKET_NAME"))
                          )
        kwargs.setdefault("access_key",
                          getattr(settings, "WASABI_GZ_ACCESS_KEY_ID",
                                  os.getenv("WASABI_GZ_ACCESS_KEY_ID"))
                          )
        kwargs.setdefault("secret_key",
                          getattr(settings, "WASABI_GZ_SECRET_ACCESS_KEY",
                                  os.getenv("WASABI_GZ_SECRET_ACCESS_KEY"))
                          )
        kwargs.setdefault("region_name",
                          getattr(settings, "WASABI_GZ_REGION_NAME", os.getenv(
                              "WASABI_GZ_REGION_NAME", "us-west-1"))
                          )
        kwargs.setdefault("endpoint_url",
                          getattr(settings, "WASABI_GZ_ENDPOINT_URL", os.getenv(
                              "WASABI_GZ_ENDPOINT_URL", "https://s3.us-west-1.wasabisys.com"))
                          )
        super().__init__(*args, **kwargs)
