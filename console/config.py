"""Console settings, from the environment, falling back to the team API client's constants.

The client (scripts/natid_ocr_pipeline_model_API_calling.py) already carries the
work machine's MongoDB, media-host and engine settings (D-108). The console reads
those constants from its source with ``ast`` instead of importing it, because
importing the client creates its log file.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / "scripts" / "natid_ocr_pipeline_model_API_calling.py"
CLIENT_CONSTANTS = ("MONGO_URI", "MONGO_DB", "MONGO_COLLECTION", "OUTPUT_FIELD", "OCR_ENGINE_URL",
                    "MEDIA_SERVER_IP", "MEDIA_SERVER_PLACEHOLDER", "FRONT_SUFFIX")


class ConfigError(ValueError):
    """A setting is missing or out of range; the console does not start."""


def client_constants(path: Path = CLIENT_FILE) -> dict[str, str]:
    """The literal string constants of the client module, without executing it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in CLIENT_CONSTANTS:
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    continue
                if isinstance(value, str):
                    found[name] = value
    return found


def _int(env: dict[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _flag(env: dict[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_db: str
    applicants: str
    result_field: str
    typed_field: str
    folder_field: str
    engine_url: str
    media_host: str
    media_placeholder: str
    front_suffix: str
    host: str = "127.0.0.1"
    port: int = 8090
    session_hours: int = 8
    cookie_secure: bool = False
    demo: bool = False
    display_fields: tuple[str, ...] = field(default_factory=tuple)
    stats_ttl_s: int = 30
    media_timeout_s: int = 15
    engine_timeout_s: int = 120

    @property
    def reviews(self) -> str:
        return "ocr_reviews"

    @property
    def audit(self) -> str:
        return "ocr_review_audit"

    @property
    def users(self) -> str:
        return "console_users"

    @property
    def sessions(self) -> str:
        return "console_sessions"

    def front_url(self, folder_path: str) -> str:
        """The same URL the API client downloads (build_front_url)."""
        return folder_path.replace(self.media_placeholder, self.media_host).rstrip("/") + self.front_suffix


def load(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    client = client_constants()

    def pick(name: str, client_name: str | None, default: str) -> str:
        value = env.get(name)
        if value:
            return value
        return client.get(client_name, default) if client_name else default

    media_host = pick("CONSOLE_MEDIA_HOST", "MEDIA_SERVER_IP", "127.0.0.1")
    settings = Settings(
        mongo_uri=pick("CONSOLE_MONGO_URI", "MONGO_URI", "mongodb://127.0.0.1:27017/"),
        mongo_db=pick("CONSOLE_MONGO_DB", "MONGO_DB", "DEPI_R5_Applicant_DB"),
        applicants=pick("CONSOLE_APPLICANTS_COLLECTION", "MONGO_COLLECTION", "applicants"),
        result_field=pick("CONSOLE_RESULT_FIELD", "OUTPUT_FIELD", "Auto Id_Validation"),
        typed_field=pick("CONSOLE_TYPED_FIELD", None, "National Id"),
        folder_field=pick("CONSOLE_FOLDER_FIELD", None, "download folder path"),
        engine_url=pick("CONSOLE_OCR_ENGINE_URL", "OCR_ENGINE_URL", "http://127.0.0.1:8081/engine"),
        media_host=media_host,
        media_placeholder=pick("CONSOLE_MEDIA_PLACEHOLDER", "MEDIA_SERVER_PLACEHOLDER", "<IP_adress>"),
        front_suffix=pick("CONSOLE_FRONT_SUFFIX", "FRONT_SUFFIX", "/Nid_Front/Nid_Front.jpg"),
        host=env.get("CONSOLE_HOST") or "127.0.0.1",
        port=_int(env, "CONSOLE_PORT", 8090, 1, 65535),
        session_hours=_int(env, "CONSOLE_SESSION_HOURS", 8, 1, 24),
        cookie_secure=_flag(env, "CONSOLE_COOKIE_SECURE"),
        demo=_flag(env, "CONSOLE_DEMO"),
        display_fields=tuple(f.strip() for f in env.get("CONSOLE_DISPLAY_FIELDS", "").split(",") if f.strip()),
        stats_ttl_s=_int(env, "CONSOLE_STATS_TTL_S", 30, 0, 3600),
        media_timeout_s=_int(env, "CONSOLE_MEDIA_TIMEOUT_S", 15, 1, 120),
        engine_timeout_s=_int(env, "CONSOLE_ENGINE_TIMEOUT_S", 120, 5, 600),
    )
    if not settings.engine_url.startswith(("http://", "https://")):
        raise ConfigError("CONSOLE_OCR_ENGINE_URL must be an http(s) URL")
    return settings
