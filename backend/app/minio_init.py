"""Idempotent bucket setup for the optional internal MinIO Compose service."""

import asyncio

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from app.adapters.storage.minio_store import MinioObjectStore
from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    store = MinioObjectStore(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
    )
    # MinIO may have passed HTTP readiness but still be starting S3 auth.
    for attempt in range(30):
        try:
            try:
                await asyncio.to_thread(store.client.head_bucket, Bucket=store.bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                    raise
                await asyncio.to_thread(store.client.create_bucket, Bucket=store.bucket)
            # Remove any older anonymous bucket policy; no public ACLs or presigned links.
            try:
                await asyncio.to_thread(store.client.delete_bucket_policy, Bucket=store.bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("NoSuchBucketPolicy", "404"):
                    raise
            print(f"Private object bucket ready: {store.bucket}")
            return
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
