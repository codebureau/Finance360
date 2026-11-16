"""Auth providers. ``REGISTRY`` maps a provider key to the provider, like ``behaviours.REGISTRY``.

``FINANCE360_AUTH_PROVIDERS`` (default ``password``) picks the active ones; the application
factory resolves them. M1 ships one.
"""

from finance360.capabilities.auth.types import AuthProvider
from finance360.plugins.auth.password import PasswordProvider

REGISTRY: dict[str, AuthProvider] = {p.key: p for p in (PasswordProvider(),)}

__all__ = ["REGISTRY"]
