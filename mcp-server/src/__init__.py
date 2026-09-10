"""Asterisk MCP Supervision Server.

Architecture: Hexagonal (Ports & Adapters) with Clean Architecture principles.

Layers:
- domain: Core business logic, independent of frameworks
- application: Use cases orchestrating domain & adapters
- adapters: Concrete implementations of domain ports (Asterisk AMI/ARI)
- interfaces: MCP tools exposed to clients
- security: Auth, RBAC, sanitization
- voice: Speech-to-Speech pipeline (Module 3), runs as its own process
"""

__version__ = "0.2.0"
