"""Service settings, read once from environment variables.

Everything that differs between a laptop and a deployment lives here, so the code
never reads os.environ anywhere else. Missing API keys stop the service from
starting unless anonymous access is asked for explicitly: a document service that
is open by accident is worse than one that refuses to start.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from doc_extract.observability.cost import PriceError, PriceTable
from doc_extract.review.queue import APPROVE_THRESHOLD

METHODS = ("text", "vision")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("var/doc_extract.db")
    storage_dir: Path = Path("var/documents")
    # API key -> the name it identifies, recorded on every review for the audit trail
    api_keys: dict[str, str] = field(default_factory=dict)
    allow_anonymous: bool = False
    provider: str = "gemini"
    primary: str = "vision"
    secondary: str | None = "text"
    repair: bool = True
    vendors_path: Path | None = None
    calibration_path: Path | None = None
    approve_threshold: float = APPROVE_THRESHOLD
    workers: int = 1
    max_attempts: int = 3
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pages: int = 5
    prices: PriceTable = field(default_factory=PriceTable)
    daily_budget_usd: float | None = None
    log_json: bool = True
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.api_keys and not self.allow_anonymous:
            raise ConfigError(
                "Set DOC_EXTRACT_API_KEYS (name:key,name:key), or DOC_EXTRACT_ALLOW_ANONYMOUS=1 "
                "for local use only"
            )
        if self.primary not in METHODS:
            raise ConfigError(f"primary method must be one of {METHODS}, not {self.primary!r}")
        if self.secondary is not None and self.secondary not in METHODS:
            raise ConfigError(f"secondary method must be one of {METHODS} or 'none'")
        if self.secondary == self.primary:
            raise ConfigError("the secondary extraction must use a different method")
        if self.workers < 1 or self.max_attempts < 1:
            raise ConfigError("workers and max attempts must be at least 1")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        defaults = cls(allow_anonymous=True)

        def path(name: str, default: Path | None) -> Path | None:
            value = env.get(name)
            return Path(value) if value else default

        secondary = env.get("DOC_EXTRACT_SECONDARY", defaults.secondary or "none")
        return cls(
            db_path=path("DOC_EXTRACT_DB", defaults.db_path),
            storage_dir=path("DOC_EXTRACT_STORAGE", defaults.storage_dir),
            api_keys=parse_api_keys(env.get("DOC_EXTRACT_API_KEYS", "")),
            allow_anonymous=env.get("DOC_EXTRACT_ALLOW_ANONYMOUS") == "1",
            provider=env.get("DOC_EXTRACT_PROVIDER", defaults.provider),
            primary=env.get("DOC_EXTRACT_PRIMARY", defaults.primary),
            secondary=None if secondary == "none" else secondary,
            repair=env.get("DOC_EXTRACT_REPAIR", "1") == "1",
            vendors_path=path("DOC_EXTRACT_VENDORS", None),
            calibration_path=path("DOC_EXTRACT_CALIBRATION", None),
            approve_threshold=float(
                env.get("DOC_EXTRACT_APPROVE_THRESHOLD", defaults.approve_threshold)
            ),
            workers=int(env.get("DOC_EXTRACT_WORKERS", defaults.workers)),
            max_attempts=int(env.get("DOC_EXTRACT_MAX_ATTEMPTS", defaults.max_attempts)),
            max_upload_bytes=int(float(env.get("DOC_EXTRACT_MAX_UPLOAD_MB", 10)) * 1024 * 1024),
            max_pages=int(env.get("DOC_EXTRACT_MAX_PAGES", defaults.max_pages)),
            prices=_prices(env.get("DOC_EXTRACT_PRICES", "")),
            daily_budget_usd=(
                float(env["DOC_EXTRACT_DAILY_BUDGET_USD"])
                if env.get("DOC_EXTRACT_DAILY_BUDGET_USD")
                else None
            ),
            log_json=env.get("DOC_EXTRACT_LOG_FORMAT", "json") == "json",
            log_level=env.get("DOC_EXTRACT_LOG_LEVEL", "INFO").upper(),
        )


def parse_api_keys(raw: str) -> dict[str, str]:
    """'alice:k1,bob:k2' -> {'k1': 'alice', 'k2': 'bob'}."""
    keys: dict[str, str] = {}
    for entry in filter(None, (part.strip() for part in raw.split(","))):
        name, sep, key = entry.partition(":")
        if not sep or not name or len(key) < 16:
            raise ConfigError(
                "each API key entry must be name:key, with a key of at least 16 characters"
            )
        keys[key] = name
    return keys


def _prices(raw: str) -> PriceTable:
    try:
        return PriceTable.parse(raw)
    except PriceError as error:
        raise ConfigError(str(error)) from error
