"""Private S3-compatible objects: stream files, never expose bucket URLs."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from pathlib import Path

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from app.domain.exceptions import StorageError


class MinioObjectStore:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        if not all((endpoint, access_key, secret_key, bucket)):
            raise StorageError("MinIO endpoint, bucket, and credentials are required")
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    def key(self, owner_id: int, job_id: str, relative: str) -> str:
        if (
            owner_id < 1
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
            or any(p in ("", ".", "..") for p in relative.split("/"))
        ):
            raise StorageError("Unsafe object key")
        return f"{owner_id}/{job_id}/{relative}"

    def uri(self, key: str) -> str:
        self._validate_key(key)
        return f"s3://{self.bucket}/{key}"

    def parse(self, uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise StorageError("Object URI is outside the configured private bucket")
        key = uri[len(prefix) :]
        self._validate_key(key)
        return key

    @staticmethod
    def _validate_key(key: str) -> None:
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or any(p in ("", ".", "..") for p in key.split("/"))
        ):
            raise StorageError("Unsafe object key")

    async def upload(self, local_path: str | Path, key: str) -> str:
        self._validate_key(key)
        try:
            await asyncio.to_thread(self.client.upload_file, str(local_path), self.bucket, key)
        except Exception as exc:
            raise StorageError("Could not publish object") from exc
        return self.uri(key)

    async def download(self, uri: str, local_path: str | Path) -> None:
        key = self.parse(uri)
        path = Path(local_path)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(self.client.download_file, self.bucket, key, str(path))
        except Exception as exc:
            raise StorageError("Could not retrieve object") from exc

    async def read_text(self, uri: str) -> str:
        key = self.parse(uri)
        try:
            result = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
            body = result["Body"]
            try:
                return (await asyncio.to_thread(body.read)).decode("utf-8")
            finally:
                body.close()
        except Exception as exc:
            raise StorageError("Could not retrieve object") from exc

    async def read_text_limited(self, uri: str, max_bytes: int) -> str:
        """Read a current immutable chapter only when its object is within a cap."""
        key = self.parse(uri)
        try:
            metadata = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
            if int(metadata["ContentLength"]) > max_bytes:
                raise StorageError("Chapter exceeds search indexing size limit")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Could not inspect chapter object") from exc
        return await self.read_text(uri)

    async def exists(self, uri: str) -> bool:
        key = self.parse(uri)
        try:
            await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError("Object availability check failed") from exc

    async def stream(self, uri: str) -> Iterator[bytes]:
        key = self.parse(uri)
        try:
            response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError("Object not available") from exc
        body = response["Body"]

        def chunks() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(chunk_size=1024 * 1024)
            finally:
                body.close()

        return chunks()

    async def delete_prefix(self, prefix: str) -> None:
        self._validate_key(prefix.rstrip("/"))
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            pages = await asyncio.to_thread(
                lambda: list(paginator.paginate(Bucket=self.bucket, Prefix=prefix))
            )
            for page in pages:
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                for i in range(0, len(objects), 1000):
                    await asyncio.to_thread(
                        self.client.delete_objects,
                        Bucket=self.bucket,
                        Delete={"Objects": objects[i : i + 1000], "Quiet": True},
                    )
        except Exception as exc:
            raise StorageError("Could not delete objects") from exc
