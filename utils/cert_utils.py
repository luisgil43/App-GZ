# utils/cert_utils.py
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.backends import default_backend
from django.core.files.storage import default_storage


def cargar_certificado(pfx_path, pfx_password):
    with default_storage.open(pfx_path, 'rb') as f:
        pfx_data = f.read()

    private_key, cert, additional = pkcs12.load_key_and_certificates(
        pfx_data, pfx_password.encode(), default_backend()
    )
    return private_key, cert


def cargar_certificado_desde_bytes(pfx_bytes, pfx_password):
    private_key, cert, additional = pkcs12.load_key_and_certificates(
        pfx_bytes, pfx_password.encode(), default_backend()
    )
    return private_key, cert
