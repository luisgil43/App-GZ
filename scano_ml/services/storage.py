import json
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from django.conf import settings


class ScanoStorageConfigurationError(RuntimeError):
    pass


class ScanoStorageError(RuntimeError):
    pass


def _required_setting(name):
    value = getattr(settings, name, "")

    if value is None:
        value = ""

    value = str(value).strip()

    if not value:
        raise ScanoStorageConfigurationError(f"Missing required setting: {name}")

    return value


@lru_cache(maxsize=1)
def get_scano_s3_client():
    """
    Crea exclusivamente el cliente S3/Wasabi de Scano ML.

    No utiliza AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY ni
    ninguna credencial WASABI_GZ_*.
    """

    access_key = _required_setting("SCANO_ML_WASABI_ACCESS_KEY_ID")
    secret_key = _required_setting("SCANO_ML_WASABI_SECRET_ACCESS_KEY")
    region = _required_setting("SCANO_ML_WASABI_REGION")
    endpoint = _required_setting("SCANO_ML_WASABI_ENDPOINT")

    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        endpoint_url=endpoint,
        config=Config(
            signature_version="s3v4",
            s3={
                "addressing_style": "path",
            },
        ),
    )


def get_scano_bucket():
    return _required_setting("SCANO_ML_WASABI_BUCKET")


def generate_presigned_put_url(
    *,
    key,
    content_type,
    expires_in=900,
):
    client = get_scano_s3_client()
    bucket = get_scano_bucket()

    try:
        return client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
    except Exception as exc:
        raise ScanoStorageError("Could not generate presigned upload URL.") from exc


def head_object(*, key):
    client = get_scano_s3_client()
    bucket = get_scano_bucket()

    try:
        response = client.head_object(
            Bucket=bucket,
            Key=key,
        )
    except ClientError as exc:
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))

        if code in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return None

        raise ScanoStorageError(f"Could not inspect object: {key}") from exc
    except Exception as exc:
        raise ScanoStorageError(f"Could not inspect object: {key}") from exc

    return {
        "key": key,
        "size": int(response.get("ContentLength", 0)),
        "content_type": response.get("ContentType"),
        "etag": str(response.get("ETag", "")).strip('"') or None,
    }


def list_all_objects():
    client = get_scano_s3_client()
    bucket = get_scano_bucket()

    objects = []
    continuation_token = None

    try:
        while True:
            request = {
                "Bucket": bucket,
                "MaxKeys": 1000,
            }

            if continuation_token:
                request["ContinuationToken"] = continuation_token

            response = client.list_objects_v2(**request)

            objects.extend(
                response.get(
                    "Contents",
                    [],
                )
            )

            if not response.get(
                "IsTruncated",
                False,
            ):
                break

            continuation_token = response.get("NextContinuationToken")

            if not continuation_token:
                break

    except Exception as exc:
        raise ScanoStorageError("Could not list Scano ML objects.") from exc

    return objects


def download_json(*, key):
    client = get_scano_s3_client()
    bucket = get_scano_bucket()

    try:
        response = client.get_object(
            Bucket=bucket,
            Key=key,
        )

        raw = response["Body"].read()

        return json.loads(raw.decode("utf-8"))

    except Exception as exc:
        raise ScanoStorageError(f"Could not read JSON object: {key}") from exc
