@AGENTS.md

# Claude Code specifics

Everything above (imported from `AGENTS.md`) is the canonical, tool-agnostic rulebook. This file
adds only what is specific to Claude Code.

## Role subagents

- Roles are `.claude/agents/{product-manager,architect,developer,tester}.md`. Route natural-language
  requests ("start a Developer pass on the import issue") to the matching subagent. Never spawn one unless the maintainer
  asks.
- **Claude owns the worktree lifecycle, not the role agent.** Launch Developer, Architect and Tester
  with `isolation: "worktree"`, so the harness provisions the worktree before the agent starts.
  Product Manager needs no worktree.
- After a pass's branch merges, Claude fast-forwards `main` and removes the worktree and local
  branch. If removal fails, check for a live process holding it before forcing anything.
- **The review slot (a sibling worktree `<repo>-review`, default port 8360) is the maintainer's.** No agent
  runs in it or writes to it.

## Model policy (subscription budget)

- **Sonnet by default** for role agents and routine passes. The agent files set `model: sonnet`.
- **Opus only where it counts:** foundational design, a hard ADR, gnarly debugging. When a task
  seems to warrant it, say so and why, and let the maintainer choose. Don't silently escalate.
- Keep turns lean: batch independent tool calls, and don't re-run passing checks without a code
  change.

## Permissions

`.claude/settings.json` allowlists routine, read-only-in-effect commands (git inspection, `uv`/`npm`
build/test/lint, `docker compose`, `gh` read and review commands, including `gh pr comment` and
`gh pr ready` so a Tester pass can post its review). It deliberately excludes
anything that merges, force-pushes or rewrites history. Those are denied or prompt every time, in
addition to the git rules above. The allowlist is project-wide; there is no per-role scoping.
