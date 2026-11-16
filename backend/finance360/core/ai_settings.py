"""AI settings, read from the environment (ADR-0021 section 4). Stdlib only: no database, no SDK.

Lives in core (not ``capabilities/ai``) because ``Settings`` holds it and ``core.migrate`` imports
``config``.

Unset provider means AI is off, quietly. A value that is set but wrong fails loudly at start-up,
like ``FINANCE360_BACKUP_TIME``. The API key is read once; it is kept out of ``repr`` and never
logged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

#: The gateway default for every task: Sonnet 5.5 (requirements section 5d).
DEFAULT_MODEL = "claude-sonnet-5-5"
#: The Ask panel's default: answering from the Guide is simple, and Haiku 5.5 is cheaper.
DEFAULT_ASK_MODEL = "claude-haiku-5-5"
DEFAULT_MONTHLY_CAP_USD = "5.00"
DEFAULT_DAILY_QUESTIONS = 50
DEFAULT_LOG_DAYS = 365
MICROS = 1_000_000


def usd_to_micros(raw: str, *, name: str = "amount") -> int:
    """A decimal string of US dollars to integer micro-dollars; no float is ever involved."""
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(f"{name} must be a decimal number of US dollars, got {raw!r}") from None
    micros = value * MICROS
    if not value.is_finite() or value < 0 or micros != micros.to_integral_value():
        raise ValueError(f"{name} must be a non-negative amount with at most 6 decimals: {raw!r}")
    return int(micros)


def micros_to_usd(micros: int) -> str:
    """Integer micro-dollars as a dollar string with cents, e.g. ``5.00``."""
    return f"{Decimal(micros) / MICROS:.2f}"


def _whole(e: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = (e.get(name) or str(default)).strip()
    try:
        number = int(raw)
    except ValueError:
        number = low - 1
    if not low <= number <= high:
        raise ValueError(f"{name} must be a whole number, {low} to {high}: {raw!r}")
    return number


def _api_key(e: Mapping[str, str]) -> str | None:
    """``FINANCE360_AI_API_KEY_FILE`` (preferred) or ``FINANCE360_AI_API_KEY``."""
    if file := e.get("FINANCE360_AI_API_KEY_FILE"):
        text = Path(file).read_text(encoding="utf-8")  # a missing file is a loud startup error
        return text.strip() or None
    return (e.get("FINANCE360_AI_API_KEY") or "").strip() or None


@dataclass(frozen=True)
class AiSettings:
    #: ``plugins.ai_providers`` key (``anthropic``). None = no model (the default): Ask searches the
    #: Guide and Rule figures are read by the word matcher (ADR-0023).
    provider: str | None = None
    #: Kept out of ``repr``; read once into the provider object.
    api_key: str | None = field(default=None, repr=False)
    model: str = DEFAULT_MODEL
    model_ask: str = DEFAULT_ASK_MODEL
    #: Rule figure capture (ADR-0022 section 5). None means the gateway default, ``model``: reading
    #: a figure off a page is where a wrong value costs most, so it gets no cheaper model.
    model_rule_capture: str | None = None
    monthly_cap_usd_micros: int = usd_to_micros(DEFAULT_MONTHLY_CAP_USD)
    daily_questions: int = DEFAULT_DAILY_QUESTIONS
    log_days: int = DEFAULT_LOG_DAYS

    def model_for(self, task: str) -> str:
        """The model a task uses: its own override, else the gateway default."""
        return {
            "ask": self.model_ask,
            "rule_capture": self.model_rule_capture or self.model,
        }.get(task, self.model)

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> AiSettings:
        provider = (e.get("FINANCE360_AI_PROVIDER") or "").strip().lower() or None
        key = _api_key(e)
        if provider == "anthropic" and key is None:
            raise ValueError(
                "FINANCE360_AI_PROVIDER=anthropic needs FINANCE360_AI_API_KEY_FILE "
                "(preferred) or FINANCE360_AI_API_KEY"
            )
        cap = (e.get("FINANCE360_AI_MONTHLY_CAP_USD") or DEFAULT_MONTHLY_CAP_USD).strip()
        return cls(
            provider=provider,
            api_key=key,
            model=(e.get("FINANCE360_AI_MODEL") or "").strip() or DEFAULT_MODEL,
            model_ask=(e.get("FINANCE360_AI_MODEL_ASK") or "").strip() or DEFAULT_ASK_MODEL,
            model_rule_capture=(e.get("FINANCE360_AI_MODEL_RULE_CAPTURE") or "").strip() or None,
            monthly_cap_usd_micros=usd_to_micros(cap, name="FINANCE360_AI_MONTHLY_CAP_USD"),
            daily_questions=_whole(
                e, "FINANCE360_AI_DAILY_QUESTIONS", DEFAULT_DAILY_QUESTIONS, 1, 10_000
            ),
            log_days=_whole(e, "FINANCE360_AI_LOG_DAYS", DEFAULT_LOG_DAYS, 1, 3650),
        )
