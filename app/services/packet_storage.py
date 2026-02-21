import os
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.client import Config
from dotenv import dotenv_values

from app.services.storage_keys import driver_packet_key, driver_packet_prefix, driver_space_marker_key


_REQUIRED_PACKET_FILES = ("mc_auth.pdf", "coi.pdf", "w9.pdf")


@lru_cache(maxsize=1)
def _storage_config() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    env_values = dotenv_values(env_path) if env_path.exists() else {}

    keys = [
        "DO_SPACES_KEY",
        "DO_SPACES_SECRET",
        "DO_SPACES_BUCKET",
        "DO_SPACES_REGION",
        "DO_SPACES_ENDPOINT",
        "PACKET_STORAGE_ROOT",
    ]
    config: dict[str, str] = {}
    for key in keys:
        config[key] = (os.getenv(key) or env_values.get(key) or "").strip()

    if not config["PACKET_STORAGE_ROOT"]:
        config["PACKET_STORAGE_ROOT"] = "/srv/gcd-data/packets"

    return config


def packet_driver_dir(driver_id: int, storage_root: str | Path | None = None) -> Path:
    root = Path(storage_root) if storage_root is not None else Path(_storage_config()["PACKET_STORAGE_ROOT"])
    return root / f"driver_{driver_id}"


def packet_file_paths_for_driver(driver_id: int, storage_root: str | Path | None = None) -> list[Path]:
    driver_dir = packet_driver_dir(driver_id, storage_root)
    files = [driver_dir / name for name in _REQUIRED_PACKET_FILES]
    return [path for path in files if path.exists()]


def _spaces_client():
    config = _storage_config()
    required = [
        config["DO_SPACES_KEY"],
        config["DO_SPACES_SECRET"],
        config["DO_SPACES_BUCKET"],
        config["DO_SPACES_REGION"],
        config["DO_SPACES_ENDPOINT"],
    ]
    if any(not value for value in required):
        return None

    return boto3.session.Session().client(
        "s3",
        region_name=config["DO_SPACES_REGION"],
        endpoint_url=config["DO_SPACES_ENDPOINT"],
        aws_access_key_id=config["DO_SPACES_KEY"],
        aws_secret_access_key=config["DO_SPACES_SECRET"],
        config=Config(signature_version="s3v4"),
    )


def ensure_driver_space(driver_id: int) -> bool:
    client = _spaces_client()
    if client is None:
        return False

    try:
        client.put_object(
            Bucket=_storage_config()["DO_SPACES_BUCKET"],
            Key=driver_space_marker_key(driver_id),
            Body=b"",
            ContentType="application/octet-stream",
        )
    except Exception:
        return False

    return True


def save_packet_file(
    driver_id: int,
    filename: str,
    file_bytes: bytes,
    *,
    storage_root: str | Path | None = None,
    content_type: str = "application/pdf",
) -> dict[str, bool]:
    result = {"local_saved": False, "spaces_saved": False}

    driver_dir = packet_driver_dir(driver_id, storage_root)
    try:
        driver_dir.mkdir(parents=True, exist_ok=True)
        (driver_dir / filename).write_bytes(file_bytes)
        result["local_saved"] = True
    except Exception:
        pass

    client = _spaces_client()
    if client is not None:
        try:
            client.put_object(
                Bucket=_storage_config()["DO_SPACES_BUCKET"],
                Key=driver_packet_key(driver_id, filename),
                Body=file_bytes,
                ContentType=content_type,
            )
            result["spaces_saved"] = True
        except Exception:
            pass

    return result


def list_uploaded_packet_docs(driver_id: int, storage_root: str | Path | None = None) -> set[str]:
    found: set[str] = set()

    client = _spaces_client()
    if client is not None:
        try:
            response = client.list_objects_v2(
                Bucket=_storage_config()["DO_SPACES_BUCKET"],
                Prefix=driver_packet_prefix(driver_id),
            )
            for item in response.get("Contents", []):
                key = item.get("Key") or ""
                filename = key.rsplit("/", 1)[-1]
                if filename in _REQUIRED_PACKET_FILES:
                    found.add(filename)
        except Exception:
            pass

    driver_dir = packet_driver_dir(driver_id, storage_root)
    for filename in _REQUIRED_PACKET_FILES:
        if (driver_dir / filename).exists():
            found.add(filename)

    return {filename.replace(".pdf", "") for filename in found}


def save_bytes_by_key(
    key: str,
    file_bytes: bytes,
    *,
    content_type: str = "application/octet-stream",
    local_root: str | Path = "/srv/gcd-data",
) -> dict[str, str | bool | None]:
    result: dict[str, str | bool | None] = {
        "local_saved": False,
        "spaces_saved": False,
        "local_path": None,
        "bucket": _storage_config().get("DO_SPACES_BUCKET") or None,
    }

    local_path = Path(local_root) / key
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(file_bytes)
        result["local_saved"] = True
        result["local_path"] = str(local_path)
    except Exception:
        pass

    client = _spaces_client()
    if client is not None:
        try:
            client.put_object(
                Bucket=_storage_config()["DO_SPACES_BUCKET"],
                Key=key,
                Body=file_bytes,
                ContentType=content_type,
            )
            result["spaces_saved"] = True
        except Exception:
            pass

    return result


def generate_presigned_get_url(bucket: str, key: str, expires_seconds: int = 3600) -> str | None:
    client = _spaces_client()
    if client is None:
        return None
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )
    except Exception:
        return None
