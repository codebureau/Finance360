"""The audit writer and the append-only guarantee, on a real migrated SQLite database."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.models.audit import AuditEvent

CTX = AuditContext("owner", "owner", "api")


def _record(session: Session) -> AuditEvent:
    return audit.record(
        session,
        CTX,
        entity_type="member",
        entity_id=7,
        action="update",
        before={"name": "A"},
        after={"name": "B"},
        summary="Edited Member 'B'",
    )


def test_record_stores_context_snapshots_and_summary(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        _record(s)
    with session_factory() as s:
        (event,) = s.scalars(select(AuditEvent))
    assert (event.entity_type, event.entity_id, event.action) == ("member", 7, "update")
    assert (event.actor_kind, event.actor_ref, event.source) == ("owner", "owner", "api")
    assert event.before == {"name": "A"}
    assert event.after == {"name": "B"}
    assert event.summary == "Edited Member 'B'"
    assert event.occurred_at is not None


def test_record_commits_and_rolls_back_with_the_callers_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(RuntimeError), session_factory.begin() as s:
        _record(s)
        raise RuntimeError("the change failed")
    with session_factory() as s:
        assert s.scalars(select(AuditEvent)).first() is None


def test_unknown_action_and_actor_kind_are_rejected(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="action"), session_factory.begin() as s:
        audit.record(s, CTX, entity_type="member", entity_id=1, action="vanish", summary="x")
    with pytest.raises(ValueError, match="actor kind"):
        AuditContext("stranger", "x", "api")


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_event SET summary = 'tampered'", "DELETE FROM audit_event"]
)
def test_the_database_rejects_update_and_delete(
    session_factory: sessionmaker[Session], statement: str
) -> None:
    with session_factory.begin() as s:
        _record(s)
    with pytest.raises(DatabaseError, match="append-only"), session_factory.begin() as s:
        s.execute(text(statement))
    with session_factory() as s:
        (event,) = s.scalars(select(AuditEvent))
    assert event.summary == "Edited Member 'B'"
