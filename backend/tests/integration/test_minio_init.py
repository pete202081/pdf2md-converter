"""Optional bucket initializer creates private bucket without public policy."""

from moto import mock_aws

from app.config import get_settings
from app.minio_init import main


async def test_bucket_initializer_is_idempotent_and_private(monkeypatch) -> None:
    monkeypatch.setenv("PDF2MD_MINIO_ENDPOINT", "http://s3.amazonaws.com")
    monkeypatch.setenv("PDF2MD_MINIO_ACCESS_KEY", "test-user")
    monkeypatch.setenv("PDF2MD_MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("PDF2MD_MINIO_BUCKET", "test-books")
    get_settings.cache_clear()
    try:
        with mock_aws():
            await main()
            await main()
    finally:
        get_settings.cache_clear()
