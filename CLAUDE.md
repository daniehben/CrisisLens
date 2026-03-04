# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

CrisisLens is a project that uses **CCPM (Claude Code Project Manager)** — a spec-driven development workflow built around GitHub Issues, Git worktrees, and parallel AI agents. The CCPM system files live in `ccpm/` and must be initialized before use.

## Initial Setup

Run this once to install dependencies, authenticate GitHub CLI, and create the `.claude/` directory structure:

```
/pm:init
```

Prerequisites: `gh` (GitHub CLI) must be installed and authenticated, and the repo must have a GitHub remote that is **not** `automazeio/ccpm`.

## Core Workflow (5 Phases)

1. **PRD** → `/pm:prd-new <feature>` — guided requirements brainstorming → saves to `.claude/prds/<feature>.md`
2. **Epic** → `/pm:prd-parse <feature>` — converts PRD to technical plan → saves to `.claude/epics/<feature>/epic.md`
3. **Decompose** → `/pm:epic-decompose <feature>` — breaks epic into numbered task files
4. **Sync** → `/pm:epic-sync <feature>` or `/pm:epic-oneshot <feature>` — pushes tasks to GitHub Issues
5. **Execute** → `/pm:issue-start <id>` — launches specialized agent in isolated Git worktree

## Key Commands

```
/pm:status          # Project dashboard
/pm:next            # Show highest-priority issue to work on
/pm:standup         # Daily standup report
/pm:blocked         # Show blocked tasks
/pm:in-progress     # List active work
/pm:help            # Full command reference

/context:create     # Generate project context docs
/context:prime      # Load context for current session
/context:update     # Refresh context after changes
```

## Architecture

- `ccpm/commands/pm/` — All `/pm:*` slash command definitions (`.md` files)
- `ccpm/scripts/pm/` — Bash implementations of PM commands
- `ccpm/agents/` — Specialized agent definitions (code-analyzer, file-analyzer, test-runner, parallel-worker)
- `ccpm/rules/` — Reusable rule files loaded into commands
- `ccpm/hooks/` — Claude hooks (e.g., automatic worktree directory handling)
- `ccpm/ccpm.config` — GitHub repo detection and `gh` CLI configuration
- `.claude/` — Created by `/pm:init`; holds project-specific prds, epics, context, and scripts

## Parallel Execution Pattern

Issues marked as parallelizable can be run simultaneously. Each `/pm:issue-start` spawns an agent in its own Git worktree, keeping context isolated. Agents return concise summaries; heavy file-reading/test-running stays inside the agent.

## GitHub Integration Notes

- All issue operations use `gh` CLI with the repo auto-detected from `git remote get-url origin`
- Override with `CCPM_GITHUB_REPO=owner/repo` environment variable
- GitHub labels `epic` and `task` are auto-created on `/pm:init`
- The `gh-sub-issue` extension (`yahsan2/gh-sub-issue`) is required for sub-issue linking
