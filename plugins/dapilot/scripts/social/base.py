"""The two contracts every social platform is built against.

    Session       one account on one platform staying signed in: an OAuth token that
                  is refreshed before it expires, or a browser profile that is kept
                  warm. It can report its state, renew itself while that is possible
                  without a person, and say plainly when a person has to log in again.
    Integration   one platform: what a post may contain, how it is submitted, how it
                  is confirmed, how its numbers are read back.

They are separate because they fail separately and run on different clocks. A
post happens when the queue says so; a session is kept alive every day whether
or not anything is posted, and a dead session is a person's job, not a retry.
`social_post.py` is the only caller of either.

An adapter is one module exposing `build(account, store_dir, config) -> Integration`.
API adapters ship here. Browser adapters live outside the plugin and are loaded
from DAPILOT_SOCIAL_ADAPTER_DIR, against the same two classes.

Nothing here decides anything about a product. The words come from
social/posts.json, the media from publish.json and design.json.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Session states. `ok` and `expiring` can post; `expiring` means keepalive should renew now.
OK, EXPIRING, NEEDS_LOGIN, CHALLENGE = "ok", "expiring", "needs_login", "challenge"


class SocialError(Exception):
    """A failure with the exit code social_post.py should end on."""

    def __init__(self, reason: str, code: int = 1, **extra):
        super().__init__(reason)
        self.reason, self.code, self.extra = reason, code, extra


@dataclass
class SessionStatus:
    state: str                       # OK | EXPIRING | NEEDS_LOGIN | CHALLENGE
    account: str
    platform: str
    expires_at: float | None = None  # epoch seconds, when the platform tells us
    checked_at: float = field(default_factory=time.time)
    note: str | None = None

    @property
    def usable(self) -> bool:
        return self.state in (OK, EXPIRING)


@dataclass
class Media:
    kind: str            # image | video
    url: str | None      # public URL, what an API platform fetches
    file: str | None     # local path, what a browser platform uploads
    alt_text: str | None = None


@dataclass
class Post:
    item: str            # the product's sha256
    platform: str
    format: str          # reel | carousel | image | video | text: the adapter lists what it takes
    caption: str
    hashtags: list[str]
    media: list[Media]
    link: str | None = None

    def text(self) -> str:
        tags = " ".join(f"#{t.lstrip('#')}" for t in self.hashtags)
        return f"{self.caption}\n\n{tags}".strip() if tags else self.caption

    def fingerprint(self) -> str:
        import hashlib
        return hashlib.sha256(json.dumps([self.platform, self.format, self.text(), [m.url or m.file for m in self.media]]).encode()).hexdigest()[:16]


@dataclass
class Limits:
    caption_max: int
    hashtags_max: int
    formats: dict[str, dict]   # format -> {"media": "image"|"video", "min": n, "max": n}
    links_clickable: bool = False


@dataclass
class Published:
    post_id: str
    url: str | None
    posted_at: float = field(default_factory=time.time)
    extra: dict = field(default_factory=dict)


class Session(ABC):
    """One account on one platform. State lives in <store>/social/sessions/<platform>/<account>.json
    (tokens) or a profile directory next to it (browsers). Both are credentials."""

    platform: str

    def __init__(self, account: str, store_dir: Path):
        self.account = account
        self.dir = store_dir / "social" / "sessions" / self.platform
        self.path = self.dir / f"{account}.json"

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def save(self, doc: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    @abstractmethod
    def status(self) -> SessionStatus:
        """Ask the platform, not the file: a token can be revoked and a cookie can die early."""

    @abstractmethod
    def refresh(self) -> SessionStatus:
        """Renew without a person if the platform allows it. Never types a password."""

    @abstractmethod
    def login_help(self) -> str:
        """What a person does when the state is needs_login or challenge."""

    def keepalive(self) -> SessionStatus:
        """The daily call: look, renew when it is due, report. Safe to run when nothing is wrong."""
        st = self.status()
        if st.state == EXPIRING:
            st = self.refresh()
        return st


class Integration(ABC):
    platform: str
    kind: str          # api | browser
    limits: Limits

    def __init__(self, session: Session, config: dict):
        self.session, self.config = session, config

    def check(self, post: Post) -> list[str]:
        """Everything wrong with a post for this platform, from `limits`. No network."""
        e, lim = [], self.limits
        if len(post.text()) > lim.caption_max:
            e.append(f"caption with hashtags is {len(post.text())} characters; {self.platform} takes {lim.caption_max}")
        if len(post.hashtags) > lim.hashtags_max:
            e.append(f"{len(post.hashtags)} hashtags; {self.platform} takes {lim.hashtags_max}")
        if "—" in post.text():
            e.append("caption has an em dash")
        fmt = lim.formats.get(post.format)
        if not fmt:
            return e + [f"format {post.format!r}: {self.platform} takes {sorted(lim.formats)}"]
        wrong = [m.kind for m in post.media if m.kind != fmt["media"]]
        if wrong or not fmt["min"] <= len(post.media) <= fmt["max"]:
            e.append(f"{post.format} takes {fmt['min']} to {fmt['max']} {fmt['media']} files; got {[m.kind for m in post.media]}")
        need = "url" if self.kind == "api" else "file"
        if any(not getattr(m, need) for m in post.media):
            e.append(f"every media entry needs a {need}: {self.platform} is driven through its {self.kind}")
        return e

    @abstractmethod
    def submit(self, post: Post, save, resume: dict | None = None) -> dict:
        """Send it. `save(dict)` persists a checkpoint and is called before the step that cannot be
        undone (the publish call, the submit click), so a crash after it is found by `confirm` and
        never answered by sending again. `resume` is the checkpoint of an earlier, unfinished attempt.
        Returns the final checkpoint: whatever `confirm` needs to find the post."""

    @abstractmethod
    def confirm(self, post: Post, submitted: dict) -> Published | None:
        """The post as the platform shows it, or None while it is not visible yet. Never sends anything."""

    @abstractmethod
    def metrics(self, published: dict) -> dict:
        """Public counts for one post."""


def to_json(obj) -> dict:
    return asdict(obj)
