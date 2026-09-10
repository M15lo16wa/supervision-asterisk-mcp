# src/audit/journal.py
"""Journal d'audit append-only en JSON Lines.

Un événement par ligne :

    {"ts":"2026-09-10T20:00:00Z","event":"tool_call","actor":"admin_demo",
     "client_id":"mcp-server","tool":"originate_call","params":{...},
     "outcome":"success","detail":null,"request_id":"..."}

Écrit à la fois dans un fichier (``AUDIT_LOG_PATH``, rotation simple par taille)
et sur le logger ``audit`` (donc stdout du conteneur → collectable par Loki, etc.).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger("audit")

_MAX_VALUE_LEN = 500
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 Mio -> .1 puis écrasement
_lock = threading.Lock()
_REDACT_KEYS = {"password", "secret", "token", "authorization", "api_key"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _REDACT_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value[:20]]
    if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
        return value[:_MAX_VALUE_LEN] + "…"
    return value


@dataclass
class AuditEvent:
    event: str                       # tool_call | rbac_denied | hitl_prompt | hitl_denied | error
    actor: str
    tool: str
    outcome: str                     # success | unauthorized | cancelled | error | prompt
    client_id: str = ""
    params: dict = field(default_factory=dict)
    detail: str | None = None
    request_id: str | None = None
    ts: str = field(default_factory=_utcnow)

    def to_json(self) -> str:
        d = asdict(self)
        d["params"] = _redact(d.get("params") or {})
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


class AuditLogger:
    def __init__(self, path: str):
        self._path = Path(path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("audit: impossible de créer %s, fichier désactivé", self._path.parent)
            self._path = None  # type: ignore[assignment]

    def _rotate_if_needed(self) -> None:
        if self._path and self._path.exists() and self._path.stat().st_size > _MAX_FILE_BYTES:
            with contextlib.suppress(OSError):
                self._path.replace(self._path.with_suffix(self._path.suffix + ".1"))

    def write(self, event: AuditEvent) -> None:
        line = event.to_json()
        logger.info("%s", line)
        if not self._path:
            return
        with _lock:
            try:
                self._rotate_if_needed()
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError as e:  # ne jamais faire échouer une action à cause de l'audit
                logger.error("audit: écriture échouée: %s", e)

    def tail(self, limit: int = 50) -> list[dict]:
        if not self._path or not self._path.exists():
            return []
        with _lock:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        out = []
        for raw in lines[-limit:]:
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out


_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(os.getenv("AUDIT_LOG_PATH", settings.audit_log_path))
    return _audit_logger


def audit_log(
    event: str,
    *,
    actor: str,
    tool: str,
    outcome: str,
    client_id: str = "",
    params: dict | None = None,
    detail: str | None = None,
    request_id: str | None = None,
) -> None:
    get_audit_logger().write(
        AuditEvent(
            event=event, actor=actor, tool=tool, outcome=outcome,
            client_id=client_id, params=params or {}, detail=detail,
            request_id=request_id,
        )
    )
