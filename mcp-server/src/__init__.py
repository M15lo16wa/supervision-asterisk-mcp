"""Asterisk MCP Supervision Server.

Architecture: Hexagonal (Ports & Adapters) with Clean Architecture principles.

Layers:
- domain: Core business logic, independent of frameworks
- application: Use cases orchestrating domain & adapters
- adapters: Concrete implementations of domain ports
- interfaces: MCP tools exposed to clients
- security: Auth, RBAC, sanitization
"""

__version__ = "0.1.0"
