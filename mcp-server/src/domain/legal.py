# src/domain/legal.py
"""Encadrement légal de l'écoute et de l'enregistrement des communications.

L'outil ``spy_channel`` (ChanSpy) porte trois exigences qui doivent rester
visibles à chaque étape du parcours, pas seulement dans la doc :

* **description de l'outil** — le client MCP lit l'avertissement avant de
  l'invoquer (exporté dans ``docs/tool-schemas.json``) ;
* **verrou préalable** — ``acknowledge_legal=true`` est exigé avant toute
  exécution, quel que soit le mode et indépendamment de ``MCP_HITL_MODE``,
  **après** le RBAC : on n'invite jamais un acteur dépourvu du droit d'écouter
  à attester d'une base légale ;
* **validation humaine** — pour ``whisper``/``barge``, le rappel est joint au
  message d'elicitation vu par la personne qui confirme.

Le refus et l'exécution sont tracés au journal d'audit, et le refus alimente
la métrique ``mcp_legal_denials_total`` pour qu'on puisse voir dans Grafana
que le verrou joue bien son rôle.
"""
from __future__ import annotations

# Avertissement complet — renvoyé au client MCP et joint à la réponse.
SPY_LEGAL_NOTICE = (
    "AVERTISSEMENT LÉGAL — L'écoute (ChanSpy) et l'enregistrement de communications "
    "sont strictement encadrés. Assurez-vous d'une base légale, de l'information "
    "préalable des personnes concernées et, le cas échéant, de leur consentement, "
    "conformément au RGPD et au droit local des télécommunications. Toute écoute "
    "est tracée dans le journal d'audit."
)

# Rappel court — joint au message HITL pour ne pas noyer la demande de confirmation.
SPY_LEGAL_REMINDER = (
    "Encadrement légal : base légale, information préalable et consentement "
    "le cas échéant (RGPD / droit des télécommunications). Toute écoute est tracée."
)
