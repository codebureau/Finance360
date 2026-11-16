# 0008. AGENTS.md as canonical agent instructions

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational)

## Context

An earlier project of the maintainer's keeps all agent rules in `CLAUDE.md`. Finance360 is open-source-ready, and contributors may
use other coding agents. Open conventions now exist: **AGENTS.md** for project instructions,
**Agent Skills** (`SKILL.md`) for skills, and **MCP** for tool access. Role subagent definitions
have no cross-tool standard: `.claude/agents/*.md` frontmatter is specific to Claude Code.

## Decision

- **`AGENTS.md` is the canonical, tool-agnostic rulebook:**
  - project summary;
  - non-negotiables;
  - vocabulary rule;
  - architecture boundaries;
  - commands;
  - testing protocol;
  - git workflow;
  - tracker conventions;
  - the role working model.
- **`CLAUDE.md` is thin:** it imports `@AGENTS.md`, then adds only Claude Code mechanics (subagent
  routing, worktree isolation, the Sonnet-by-default model policy, permission notes).
- **Role subagents stay in `.claude/agents/`**, thin, with the substance of each role in
  `AGENTS.md`.
- **Project skills use the Agent Skills format.**

## Consequences

- One source of truth, readable by any agent. No duplicated rules to drift apart.
- Diverges in layout from that earlier project, intentionally. It could adopt the same split later.
