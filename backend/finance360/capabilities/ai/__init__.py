"""The AI gateway Capability (ADR-0005, ADR-0021): redaction, the AI log, caps and
the provider seam.

It reads core only. Features (the Ask panel; later import mapping) call ``gateway.complete`` with a
data class; they never reach a provider directly.
"""
