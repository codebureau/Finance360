"""Authentication (ADR-0010): the provider seam, Sessions, lockout and TOTP.

Independent of the other Capabilities. Providers (``plugins/auth``) sit above and are wired in by
the application factory; nothing here imports them.
"""
