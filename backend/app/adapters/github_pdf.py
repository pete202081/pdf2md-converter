"""Bounded GitHub PDF discovery and pinned, authenticated retrieval.

Only this adapter can call GitHub. Client input is never treated as a URL.
"""

import asyncio
import hashlib
import re
import tempfile
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import quote, urlsplit

import httpx
from fastapi import UploadFile

from app.domain.exceptions import ConflictError, NotFoundError, ProcessingError, ValidationError

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_SHA = re.compile(r"^[a-fA-F0-9]{40}$")
_MAX_JSON = 3 * 1024 * 1024
_REDIRECT_HOSTS = {"api.github.com", "raw.githubusercontent.com", "objects.githubusercontent.com"}


def validate_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/")
    if len(parts) != 2 or not all(
        _NAME.fullmatch(item) and item not in (".", "..") for item in parts
    ):
        raise ValidationError("Enter a repository as owner/name")
    return parts[0], parts[1]


def validate_path(path: str) -> list[str]:
    parts = path.split("/")
    if (
        not path
        or len(path) > 900
        or len(parts) > 24
        or any(
            part in ("", ".", "..") or "\\" in part or any(ord(c) < 32 for c in part)
            for part in parts
        )
    ):
        raise ValidationError("Invalid repository file path")
    return parts


def validate_sha(sha: str) -> str:
    if not _SHA.fullmatch(sha):
        raise ValidationError("Invalid Git revision")
    return sha.lower()


class GitHubPDF:
    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not token:
            raise ConflictError("Ask the administrator to configure a GitHub token in Settings")
        self.token = token
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=httpx.Timeout(12, read=25),
            transport=self.transport,
            follow_redirects=False,
        )

    def _headers(self, *, raw: bool = False) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pdf2md-converter",
        }

    @staticmethod
    def _status(status: int) -> None:
        if status == 404:
            raise NotFoundError("GitHub repository or file is unavailable")
        if status in (401, 403, 429):
            raise ConflictError(
                "GitHub access denied or rate limited; check the token and retry later"
            )
        if status >= 400:
            raise ProcessingError("GitHub could not complete the request")

    async def _json(self, path: str, params: dict[str, str | int] | None = None) -> dict | list:
        try:
            async with (
                self._client() as client,
                client.stream("GET", path, params=params, headers=self._headers()) as response,
            ):
                self._status(response.status_code)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_JSON:
                        raise ValidationError(
                            "GitHub listing is too large; browse a smaller folder"
                        )
                result = httpx.Response(200, content=bytes(data)).json()
                if not isinstance(result, (dict, list)):
                    raise ValueError("Invalid GitHub response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProcessingError("GitHub is unavailable or returned an invalid response") from exc

    async def repository(self, repo: str, *, admin: bool) -> dict:
        owner, name = validate_repo(repo)
        result = await self._json(f"/repos/{owner}/{name}")
        if not isinstance(result, dict) or not isinstance(result.get("private"), bool):
            raise ProcessingError("Invalid GitHub repository metadata")
        if result["private"] and not admin:
            raise NotFoundError("GitHub repository or file is unavailable")
        return result

    async def check(self) -> bool:
        data = await self._json("/user")
        return isinstance(data, dict) and isinstance(data.get("login"), str)

    async def private_repositories(self, page: int) -> list[dict]:
        data = await self._json(
            "/user/repos", {"visibility": "private", "per_page": 20, "page": min(max(page, 1), 10)}
        )
        if not isinstance(data, list):
            raise ProcessingError("Invalid GitHub repository listing")
        return [
            {"repo": r["full_name"], "private": True}
            for r in data
            if isinstance(r, dict)
            and r.get("private") is True
            and isinstance(r.get("full_name"), str)
        ]

    async def browse(self, repo: str, directory: str, *, admin: bool) -> dict:
        info = await self.repository(repo, admin=admin)
        owner, name = validate_repo(repo)
        path = (
            "/".join(quote(part, safe="") for part in validate_path(directory)) if directory else ""
        )
        branch = info.get("default_branch")
        if not isinstance(branch, str) or not branch or len(branch) > 200:
            raise ProcessingError("GitHub repository has no default branch")
        commit = await self._json(f"/repos/{owner}/{name}/commits/{quote(branch, safe='')}")
        revision = commit.get("sha") if isinstance(commit, dict) else None
        revision = validate_sha(revision) if isinstance(revision, str) else None
        if not revision:
            raise ProcessingError("Could not resolve GitHub revision")
        listing = await self._json(f"/repos/{owner}/{name}/contents/{path}", {"ref": revision})
        if not isinstance(listing, list):
            raise ValidationError("Select a folder rather than an individual file")
        if len(listing) > 1000:
            raise ValidationError("Folder has too many entries; choose a narrower repository")
        files = [
            self._file(item, str(info["full_name"]), revision)
            for item in listing
            if isinstance(item, dict)
            and item.get("type") == "file"
            and isinstance(item.get("name"), str)
            and item["name"].lower().endswith(".pdf")
        ]
        folders = [
            str(item["path"])
            for item in listing
            if isinstance(item, dict)
            and item.get("type") == "dir"
            and isinstance(item.get("path"), str)
        ]
        return {
            "repo": info["full_name"],
            "private": info["private"],
            "directory": directory,
            "revision": revision,
            "files": files,
            "folders": folders,
            "incomplete": len(listing) == 1000,
        }

    @staticmethod
    def _file(item: dict, repo: str, revision: str) -> dict:
        path = str(item.get("path", ""))
        validate_path(path)
        sha = validate_sha(str(item.get("sha", "")))
        owner, name = validate_repo(repo)
        link = f"https://github.com/{owner}/{name}/blob/{revision}/" + "/".join(
            quote(part, safe="") for part in path.split("/")
        )
        return {
            "repo": repo,
            "path": path,
            "sha": sha,
            "revision": revision,
            "size": item.get("size") if isinstance(item.get("size"), int) else None,
            "url": link,
        }

    async def search(self, query: str, page: int) -> dict:
        query = query.strip()
        if not 2 <= len(query) <= 80 or not re.fullmatch(r"[\w .-]+", query):
            raise ValidationError("Search for a book or subject using 2–80 plain characters")
        page = min(max(page, 1), 5)
        data = await self._json(
            "/search/repositories",
            {"q": f"{query} in:name,description,readme is:public", "per_page": 5, "page": page},
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ProcessingError("Invalid GitHub search results")
        results: list[dict] = []
        for item in data["items"][:5]:
            if not isinstance(item, dict) or item.get("private") is not False:
                continue  # defense in depth: never expose private metadata to non-admin callers
            repo = item.get("full_name")
            if not isinstance(repo, str):
                continue
            try:
                owner, name = validate_repo(repo)
                # A repository could turn private between search and tree reads.
                current = await self.repository(repo, admin=False)
                branch = current.get("default_branch")
                if not isinstance(branch, str) or not branch:
                    continue
                commit = await self._json(f"/repos/{owner}/{name}/commits/{quote(branch, safe='')}")
                revision = (
                    validate_sha(str(commit.get("sha", ""))) if isinstance(commit, dict) else ""
                )
                tree = await self._json(
                    f"/repos/{owner}/{name}/git/trees/{revision}", {"recursive": 1}
                )
                if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
                    continue
                files = [
                    self._file(entry, repo, revision)
                    for entry in tree["tree"][:20000]
                    if isinstance(entry, dict)
                    and entry.get("type") == "blob"
                    and isinstance(entry.get("path"), str)
                    and entry["path"].lower().endswith(".pdf")
                ]
                # Recheck visibility after network I/O before returning paths.
                await self.repository(repo, admin=False)
                results.append(
                    {
                        "repo": repo,
                        "files": files,
                        "incomplete": bool(tree.get("truncated")) or len(tree["tree"]) > 20000,
                    }
                )
            except NotFoundError:
                continue  # a former public repo became private or unavailable
            except (ValidationError, ProcessingError):
                # Revalidate before exposing even the repository name.
                try:
                    await self.repository(repo, admin=False)
                except (NotFoundError, ProcessingError):
                    continue
                results.append({"repo": repo, "files": [], "incomplete": True})
        return {
            "repositories": results,
            "page": page,
            "incomplete": bool(data.get("incomplete_results")) or len(data["items"]) > 5,
            "has_more": len(data["items"]) == 5 and page < 5,
        }

    async def download(
        self, repo: str, path: str, revision: str, sha: str, max_bytes: int
    ) -> UploadFile:
        owner, name = validate_repo(repo)
        parts = validate_path(path)
        if not parts[-1].lower().endswith(".pdf"):
            raise ValidationError("Select a PDF file")
        revision, sha = validate_sha(revision), validate_sha(sha)
        endpoint = f"/repos/{owner}/{name}/contents/" + "/".join(quote(p, safe="") for p in parts)
        metadata = await self._json(endpoint, {"ref": revision})
        if (
            not isinstance(metadata, dict)
            or metadata.get("type") != "file"
            or metadata.get("sha") != sha
        ):
            raise ConflictError("GitHub file changed since selection; browse and select it again")
        size = metadata.get("size")
        if not isinstance(size, int) or size < 1 or size > max_bytes:
            raise ValidationError("GitHub PDF is empty or exceeds the configured PDF size limit")
        temp = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")  # noqa: SIM115 - caller owns and closes returned file
        try:
            async with self._client() as client:
                target: str = endpoint
                params: dict[str, str] | None = {"ref": revision}
                for attempt in range(3):
                    parsed = urlsplit(target)
                    host = parsed.hostname if parsed.scheme else "api.github.com"
                    if (
                        host not in _REDIRECT_HOSTS
                        or (parsed.scheme and parsed.scheme != "https")
                        or parsed.port not in (None, 443)
                        or parsed.username
                    ):
                        raise ValidationError("Unexpected GitHub download destination")
                    headers = (
                        self._headers(raw=True)
                        if host == "api.github.com"
                        else {"User-Agent": "pdf2md-converter"}
                    )
                    async with client.stream(
                        "GET", target, params=params, headers=headers
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if attempt == 2:
                                raise ProcessingError("GitHub redirected too many times")
                            location = response.headers.get("location", "")
                            if not location.startswith("https://"):
                                raise ValidationError("Unexpected GitHub download destination")
                            target, params = location, None
                            continue
                        self._status(response.status_code)
                        count = 0
                        async for chunk in response.aiter_bytes():
                            count += len(chunk)
                            if count > max_bytes:
                                raise ValidationError(
                                    "GitHub PDF exceeds the configured PDF size limit"
                                )
                            await asyncio.to_thread(temp.write, chunk)
                        if count != size:
                            raise ConflictError(
                                "GitHub file changed during download; select it again"
                            )
                        break
                await asyncio.to_thread(temp.seek, 0)
                head = await asyncio.to_thread(temp.read, 1024)
                if head.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise ValidationError(
                        "Git LFS pointers are not supported; upload the PDF itself"
                    )
                if b"%PDF-" not in head:
                    raise ValidationError("GitHub file is not a valid PDF")
                await asyncio.to_thread(temp.seek, 0)
                digest = hashlib.sha1(usedforsecurity=False)
                digest.update(f"blob {count}\0".encode())
                while block := await asyncio.to_thread(temp.read, 1024 * 1024):
                    digest.update(block)
                if digest.hexdigest() != sha:
                    raise ConflictError("GitHub file changed during download; select it again")
                await asyncio.to_thread(temp.seek, 0)
                return UploadFile(file=cast(BinaryIO, temp), filename=Path(path).name)
        except (httpx.HTTPError, OSError) as exc:
            temp.close()
            raise ProcessingError("Could not retrieve PDF from GitHub") from exc
        except Exception:
            temp.close()
            raise
