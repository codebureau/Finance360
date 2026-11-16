"""What the gateway raises (ADR-0021). Pure: the API and features import these, not the gateway.

``message`` is calm wording safe to show; it never carries a request, a reply or a credential.
"""

from __future__ import annotations

from datetime import date

MONTHLY_CAP = "monthly_cap"
DAILY_LIMIT = "daily_limit"


class AiError(Exception):
    """Base of what the gateway raises. ``message`` is calm wording safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AiOff(AiError):
    def __init__(self) -> None:
        super().__init__("AI is not switched on.")


class CapReached(AiError):
    def __init__(self, reason: str, resets_on: date | None) -> None:
        super().__init__(
            "The monthly AI limit has been reached."
            if reason == MONTHLY_CAP
            else "The daily limit of AI questions has been reached."
        )
        self.reason = reason
        self.resets_on = resets_on


class Busy(AiError):
    def __init__(self) -> None:
        super().__init__("One moment: another question is being answered.")


class AiUnavailable(AiError):
    """The provider failed; the message is the provider's own calm wording."""
