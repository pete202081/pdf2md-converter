"""Database-backed runtime overrides; infrastructure remains environment-configured."""

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import RuntimeSetting
from app.domain.exceptions import ValidationError

FIELDS = frozenset(
    {
        "vlm_provider",
        "ollama_base_url",
        "ollama_vision_model",
        "openrouter_vision_model",
        "openrouter_site_url",
        "openrouter_app_name",
        "openai_vision_model",
        "anthropic_vision_model",
        "vlm_timeout_seconds",
        "max_pages",
        "max_upload_bytes",
    }
)
KEYS = frozenset({"openrouter_api_key", "openai_api_key", "anthropic_api_key"})


def _cipher(path: str) -> Fernet:
    file = Path(path)
    file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if file.is_file():
        return Fernet(file.read_bytes())
    # Publish a complete key atomically, even if API and worker save concurrently.
    with tempfile.NamedTemporaryFile(dir=file.parent, delete=False) as handle:
        os.chmod(handle.name, 0o600)
        handle.write(Fernet.generate_key())
        temporary = Path(handle.name)
    try:
        with suppress(FileExistsError):
            os.link(temporary, file)
    finally:
        temporary.unlink()
    return Fernet(file.read_bytes())


class RuntimeSettingsService:
    def __init__(self, session: AsyncSession, defaults: Settings) -> None:
        self.session = session
        self.defaults = defaults

    async def _entries(self) -> dict[str, str]:
        rows = (await self.session.scalars(select(RuntimeSetting))).all()
        return {row.key: row.value for row in rows}

    async def resolve(self) -> Settings:
        entries = await self._entries()
        overrides: dict[str, object] = {}
        for key, value in entries.items():
            if key in FIELDS:
                overrides[key] = value
            elif key in KEYS:
                overrides[key] = (
                    _cipher(self.defaults.settings_key_file).decrypt(value.encode()).decode()
                )
        return Settings(**(self.defaults.model_dump() | overrides))

    async def public(self) -> dict[str, object]:
        effective = await self.resolve()
        return {
            **{key: getattr(effective, key) for key in FIELDS},
            "keys_configured": {key: bool(getattr(effective, key)) for key in KEYS},
        }

    async def save(
        self, values: dict[str, Any], secrets: dict[str, str | None]
    ) -> dict[str, object]:
        if not values.keys() <= FIELDS or not secrets.keys() <= KEYS:
            raise ValidationError("Unsupported setting")
        if "max_pages" in values and not 1 <= int(values["max_pages"]) <= 10000:
            raise ValidationError("Maximum pages must be between 1 and 10000")
        if (
            "max_upload_bytes" in values
            and not 1024 <= int(values["max_upload_bytes"]) <= 50 * 1024 * 1024
        ):
            raise ValidationError(
                "PDF limit must be between 1 KiB and 50 MiB (nginx has a separate limit)"
            )
        if "vlm_timeout_seconds" in values and not 5 <= float(values["vlm_timeout_seconds"]) <= 600:
            raise ValidationError("Vision timeout must be between 5 and 600 seconds")
        if "ollama_base_url" in values and not str(values["ollama_base_url"]).startswith(
            ("http://", "https://")
        ):
            raise ValidationError("Ollama URL must begin with http:// or https://")
        for key, value in values.items():
            if isinstance(value, str) and len(value) > 512:
                raise ValidationError(f"Setting {key} is too long")
        for key, value in secrets.items():
            if value is not None and len(value) > 4096:
                raise ValidationError(f"Key {key} is too long")
        current = await self.resolve()
        try:
            Settings(**(current.model_dump() | values | {k: v or "" for k, v in secrets.items()}))
        except PydanticValidationError as exc:
            raise ValidationError("Invalid settings values") from exc
        for key, value in values.items():
            await self.session.merge(RuntimeSetting(key=key, value=str(value)))
        for key, value in secrets.items():
            encrypted = _cipher(self.defaults.settings_key_file).encrypt((value or "").encode())
            await self.session.merge(RuntimeSetting(key=key, value=encrypted.decode()))
        await self.session.commit()
        return await self.public()
