from __future__ import annotations

import json
from pathlib import Path

from .models import UserCtx


class UserDirectory:
    """Maps an authenticated email to groups.

    Demo: reads data/users.json. In a real deployment this would call the Admin SDK Directory API
    (or use the identity provider's group claims). Group names are the same strings that appear
    in document ACLs as `group:<name>`.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def get(self, email: str) -> UserCtx | None:
        rec = self._load().get(email.lower())
        if rec is None:
            return None
        return UserCtx(email=email.lower(), display_name=rec.get("display_name", ""), groups=list(rec.get("groups", [])))

    def all(self) -> list[UserCtx]:
        return [
            UserCtx(email=e, display_name=r.get("display_name", ""), groups=list(r.get("groups", [])))
            for e, r in self._load().items()
        ]
