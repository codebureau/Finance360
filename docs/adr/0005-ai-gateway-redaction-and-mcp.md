# 0005. AI gateway, redaction and MCP

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §3, §5a, §5d, §5e)

## Context

AI will assist with import mapping, document extraction, narrative and Q&A, and eventually a
personal finance agent with durable Knowledge. The data is highly sensitive. The principle is
**"AI proposes, deterministic code disposes."** Local models (Ollama) are available but noticeably
weaker.

## Decision

- **Every AI call goes through a thin internal AI gateway.** The gateway:
  - **redacts:** identifiers such as account numbers, names, addresses and payment references are
    tokenised before sending and restored in responses;
  - **enforces data classes per task:**
    - *mapping:* structure plus a few redacted rows;
    - *analysis:* aggregates and holdings, never raw documents;
    - *extraction:* the most sensitive class, flagged;
  - **chooses the provider and model per task from configuration;**
  - **writes a local AI log** of what was sent, to which provider and why, with cost;
  - **enforces a monthly spend cap.**
- **Claude is the default provider** (Anthropic Python SDK): Sonnet for most tasks, Haiku for cheap
  classification, Opus for hard extraction. Ollama plugs in behind the same gateway. **No agent
  framework.**
- **AI output never writes figures.** Proposals (import mappings, inferred Knowledge) need human
  confirmation or deterministic validation. A confirmed mapping becomes a deterministic Import
  profile.
- **One service layer exposed as tools two ways:** an **MCP server** (Python MCP SDK; **read-only
  first**, an early candidate milestone after M1) and, later, in-app tool use.
- No AI in M1–M3. (Amended by ADR-0021: the Ask panel brings the gateway's first thin form into the
  Orientation milestone, alongside M3, for guidance from the Guide only.)

## Consequences

- What left the network is always auditable, and the provider is swappable per task.
- The redaction layer must be tested like any other correctness-critical code.
- MCP gives Claude Code and Claude Desktop access to the household picture early, at low cost.
