"""Journal d'audit (A.6) : chaque action outil, refus RBAC et confirmation HITL
est tracé en JSON Lines (acteur, outil, paramètres, résultat, horodatage)."""
from src.audit.journal import AuditEvent, audit_log, get_audit_logger

__all__ = ["AuditEvent", "audit_log", "get_audit_logger"]
