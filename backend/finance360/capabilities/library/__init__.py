"""The Library: one content-addressed document store and generic Document links (ADR-0026).

Layered below position and above documents (``projection > tax > position > library > documents >
household``). It imports core and the loaders of Document intake, and **no record-owning
Capability**: tax, position and the rest build their own ``LinkKind`` and ``main.py`` registers it.
"""
