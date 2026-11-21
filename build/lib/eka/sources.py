"""Document sources.

LocalFolderSource  - synthetic corpus on disk (default; fully tested offline).
DriveSource        - Google Drive folder via the Drive API. NOT tested against live Drive in this repo's
                     offline test run; see docs/SETUP_GOOGLE.md.

ACL strings: `user:<email>` and `group:<name>`. Everyone in the company is in `group:all-staff`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Protocol

from .models import Document, UserCtx


class Source(Protocol):
    name: str

    def iter_documents(self) -> Iterator[Document]: ...

    def check_access(self, file_id: str, user: UserCtx) -> bool: ...


class LocalFolderSource:
    name = "local"

    def __init__(self, corpus_dir: Path):
        self.dir = Path(corpus_dir)

    def _manifest(self) -> dict:
        p = self.dir / "manifest.json"
        return json.loads(p.read_text())["files"] if p.exists() else {}

    def iter_documents(self) -> Iterator[Document]:
        for file_id, m in self._manifest().items():
            path = self.dir / m["path"]
            if not path.exists():
                continue
            yield Document(
                file_id=file_id,
                title=m["title"],
                text=path.read_text(),
                owner=m.get("owner", ""),
                allowed_principals=list(m["allowed_principals"]),
                url=f"local://{file_id}",
                modified_at=m.get("modified_at", ""),
            )

    def check_access(self, file_id: str, user: UserCtx) -> bool:
        """Live re-check against the current manifest (catches ACL changes between syncs)."""
        m = self._manifest().get(file_id)
        return bool(m and user.principals.intersection(m["allowed_principals"]))


class DriveSource:
    """Google Drive folder source (untested against live Drive here)."""

    name = "drive"
    DOC = "application/vnd.google-apps.document"
    SHEET = "application/vnd.google-apps.spreadsheet"

    def __init__(self, credentials, folder_id: str, company_domain: str):
        from googleapiclient.discovery import build

        self.svc = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.folder_id = folder_id
        self.domain = company_domain

    def _principals(self, file_id: str) -> list[str]:
        resp = self.svc.permissions().list(
            fileId=file_id, fields="permissions(type,role,emailAddress,domain)", supportsAllDrives=True
        ).execute()
        out: list[str] = []
        for p in resp.get("permissions", []):
            t = p.get("type")
            if t == "user" and p.get("emailAddress"):
                out.append(f"user:{p['emailAddress'].lower()}")
            elif t == "group" and p.get("emailAddress"):
                out.append(f"group:{p['emailAddress'].lower()}")
            elif t == "domain" and p.get("domain") == self.domain:
                out.append("group:all-staff")
            # type "anyone" is deliberately NOT mapped: public links should not widen internal access
        return out

    def iter_documents(self) -> Iterator[Document]:
        page = None
        q = f"'{self.folder_id}' in parents and trashed = false"
        while True:
            resp = self.svc.files().list(
                q=q, pageSize=100, pageToken=page, supportsAllDrives=True, includeItemsFromAllDrives=True,
                fields="nextPageToken, files(id,name,mimeType,modifiedTime,owners(emailAddress),webViewLink)",
            ).execute()
            for f in resp.get("files", []):
                text = self._export(f)
                if text is None:
                    continue
                yield Document(
                    file_id=f["id"], title=f["name"], text=text,
                    owner=(f.get("owners") or [{}])[0].get("emailAddress", ""),
                    allowed_principals=self._principals(f["id"]), url=f.get("webViewLink", ""),
                    mime_type=f["mimeType"], modified_at=f.get("modifiedTime", ""),
                )
            page = resp.get("nextPageToken")
            if not page:
                break

    def _export(self, f: dict) -> str | None:
        mt = f["mimeType"]
        if mt == self.DOC:
            data = self.svc.files().export(fileId=f["id"], mimeType="text/plain").execute()
        elif mt == self.SHEET:
            data = self.svc.files().export(fileId=f["id"], mimeType="text/csv").execute()
        elif mt.startswith("text/"):
            data = self.svc.files().get_media(fileId=f["id"]).execute()
        else:
            return None  # PDFs, images etc. are out of scope for v1
        return data.decode("utf-8", "ignore") if isinstance(data, bytes) else str(data)

    def check_access(self, file_id: str, user: UserCtx) -> bool:
        try:
            return bool(user.principals.intersection(self._principals(file_id)))
        except Exception:  # noqa: BLE001 - fail closed
            return False
