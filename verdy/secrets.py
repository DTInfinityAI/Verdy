"""Credentials handling: API keys are read, used and fingerprinted, never exposed.

Rules this module enforces:

* **Where keys come from.** Only environment variables or a private secrets file
  (``KEY=value`` lines; path from ``VERDY_SECRETS_FILE``, default
  ``~/.config/verdy/secrets.env``). Run configs may name a secret, never contain one;
  :func:`find_credentials` is used to reject configs that do.
* **No accidental display.** A :class:`Secret` prints as its name, source and
  fingerprint. The value is only available through :meth:`Secret.reveal`, and secrets
  refuse to be pickled.
* **Fingerprints, not values.** Evidence reports record which key was used as a
  one-way hash (:func:`fingerprint`): ``sha256`` (truncated SHA-256), ``hmac``
  (HMAC-SHA256 keyed with the ``VERDY_FINGERPRINT_KEY`` secret, which also stops anyone
  from confirming a guessed key against the report), or ``none``.
* **Redaction.** Every revealed value is registered with :data:`REDACTOR`, which also
  matches common key formats. Errors, logs, subprocess output and reports pass through
  it before they are shown or written.
* **Least privilege.** Subprocesses get a minimal environment containing only the
  secrets they are allowed (:func:`subprocess_env`).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

DEFAULT_SECRETS_FILE = Path.home() / ".config" / "verdy" / "secrets.env"
FINGERPRINT_KEY_NAME = "VERDY_FINGERPRINT_KEY"
FINGERPRINT_MODES = ("sha256", "hmac", "none")

# Common credential formats, used both to redact and to reject configs containing keys.
CREDENTIAL_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),  # Anthropic
    re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),  # GitHub
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
]
_BASE_ENV = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TZ", "SYSTEMROOT",
             "CUDA_VISIBLE_DEVICES", "DISPLAY", "PYTHONUNBUFFERED")

logger = logging.getLogger(__name__)


class SecretError(RuntimeError):
    """Raised when a secret is missing, unreadable, or found where it must not be."""


class Redactor:
    """Replaces known secret values and credential-shaped strings with ``[REDACTED]``."""

    MASK = "[REDACTED]"

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: str) -> None:
        if value and len(value) >= 8:
            self._values.add(value)

    def redact(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, self.MASK)
        for pattern in CREDENTIAL_PATTERNS:
            text = pattern.sub(self.MASK, text)
        return text

    def scrub(self, obj: Any) -> Any:
        """Redact every string inside a JSON-like structure."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self.scrub(v) for v in obj)
        return obj


REDACTOR = Redactor()


def redact(text: str) -> str:
    return REDACTOR.redact(text)


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def fingerprint(value: str, mode: str = "sha256", key: str | None = None) -> str | None:
    """One-way identifier of a secret, safe to store in reports."""
    if mode == "none":
        return None
    if mode == "sha256":
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    if mode == "hmac":
        if not key:
            raise SecretError(
                f"hmac fingerprints need the {FINGERPRINT_KEY_NAME} secret; set it or use "
                "fingerprint mode 'sha256'"
            )
        digest = hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256)
        return "hmac-sha256:" + digest.hexdigest()[:16]
    raise SecretError(f"unknown fingerprint mode {mode!r}; use one of {FINGERPRINT_MODES}")


class Secret:
    """A credential that does not reveal its value unless asked explicitly."""

    __slots__ = ("name", "source", "_value")

    def __init__(self, name: str, value: str, source: str) -> None:
        self.name = name
        self.source = source
        self._value = value
        REDACTOR.register(value)

    def reveal(self) -> str:
        """The secret value. Pass it straight to the client that needs it."""
        return self._value

    def fingerprint(self, mode: str = "sha256") -> str | None:
        key = None
        if mode == "hmac":
            fp_key = get_secret(FINGERPRINT_KEY_NAME, required=False)
            key = fp_key.reveal() if fp_key else None
        return fingerprint(self._value, mode, key)

    def describe(self, mode: str = "sha256") -> dict[str, Any]:
        """What reports record about this secret: never the value."""
        return {"source": self.source, "fingerprint": self.fingerprint(mode)}

    def __repr__(self) -> str:
        return f"Secret(name={self.name!r}, source={self.source!r}, {self.fingerprint()})"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        raise TypeError("secrets cannot be pickled")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and hmac.compare_digest(self._value, other._value)

    def __hash__(self) -> int:
        return hash((self.name, self.fingerprint()))


def secrets_file_path() -> Path:
    return Path(os.environ.get("VERDY_SECRETS_FILE", DEFAULT_SECRETS_FILE)).expanduser()


def _read_secrets_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    if os.name == "posix":
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretError(
                f"{path} is readable by other users; run: chmod 600 {path}"
            )
    values: dict[str, str] = {}
    for n, raw in enumerate(path.read_text("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            raise SecretError(f"{path}:{n}: expected KEY=value")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def get_secret(name: str, *, required: bool = True) -> Secret | None:
    """Look up a secret by name: environment first, then the secrets file."""
    value = os.environ.get(name)
    if value:
        return Secret(name, value, "env")
    path = secrets_file_path()
    file_values = _read_secrets_file(path)
    if file_values.get(name):
        return Secret(name, file_values[name], f"file:{path.name}")
    if required:
        raise SecretError(
            f"secret {name} is not set: export it as an environment variable or add it to "
            f"{path} (chmod 600)"
        )
    return None


def describe_secrets(names: Iterable[str], mode: str = "sha256") -> dict[str, Any]:
    """Report entry for each named secret: source and fingerprint, or ``set: false``."""
    if mode not in FINGERPRINT_MODES:
        raise SecretError(f"unknown fingerprint mode {mode!r}; use one of {FINGERPRINT_MODES}")
    out: dict[str, Any] = {}
    for name in sorted(set(names)):
        secret = get_secret(name, required=False)
        out[name] = {"set": True, **secret.describe(mode)} if secret else {"set": False}
    return out


def subprocess_env(allow: Iterable[str] = (), extra: Mapping[str, str] | None = None,
                   *, required: Iterable[str] = ()) -> dict[str, str]:
    """A minimal environment for a child process with only the allowed secrets.

    Secrets in ``required`` must be available; ``allow`` ones are passed when set.
    """
    env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
    for name in set(allow) | set(required):
        secret = get_secret(name, required=name in set(required))
        if secret:
            env[name] = secret.reveal()
    env.update(extra or {})
    return env


def find_credentials(obj: Any, path: str = "") -> list[str]:
    """Paths inside a JSON-like structure whose values look like credentials."""
    found: list[str] = []
    if isinstance(obj, str):
        if REDACTOR.redact(obj) != obj:
            found.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found += find_credentials(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found += find_credentials(v, f"{path}[{i}]")
    return found


def scan_files(paths: Iterable[str | Path]) -> list[tuple[Path, int]]:
    """``(file, line)`` of every credential-looking string in the given files."""
    hits: list[tuple[Path, int]] = []
    for p in paths:
        path = Path(p)
        files = [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else [path]
        for f in files:
            if ".git" in f.parts:
                continue
            try:
                text = f.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                if REDACTOR.redact(line) != line:
                    hits.append((f, n))
    return hits
