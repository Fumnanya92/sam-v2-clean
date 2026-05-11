# Sam v2 Master Migration Plan

## Working Agreement

Sam v2 is a clean rebuild destination, but old Sam is still the source/reference implementation.

We are not throwing old Sam away blindly.

Workflow:

1. Inspect old Sam feature.
2. Understand what works and what is broken.
3. Rebuild or copy the useful part into `sam_v2/`.
4. Run a live test.
5. Update tracker and logs.
6. Only after the new feature works, archive/delete the old feature with user approval.

## Roles

- ChatGPT: planning, architecture, review, tracking, and Codex execution briefs.
- Codex: code inspection, edits, live tests, commits, and implementation.

## Long-Term Product Vision

Sam should become a local autonomous assistant that lives on the user's machine and helps run real work.

Sam should eventually be able to:

- understand tasks and goals
- plan work
- use local tools/workers
- inspect projects and files
- run terminal commands safely
- run tests/builds
- catch and explain its own errors
- use browser/vision capabilities later
- delegate coding work to stronger agents where needed
- ask approval before sensitive actions
- keep logs for every run
- improve itself through controlled feature upgrades

Sam should act like a supervisor/chief-of-staff assistant, not just a chatbot.

## Non-Negotiable Rules

1. Migrate one feature at a time.
2. Live test every migrated feature.
3. Do not delete old files until the migrated version passes and the user approves.
4. Every feature must have logs or visible failure output.
5. Sam must catch its own errors and return structured failure information.
6. Sensitive actions require approval.
7. Keep the new code inside `sam_v2/` unless there is a clear reason not to.

## Migration Status Source

Use:

- `sam_v2/docs/FEATURE_INVENTORY.md`
- `sam_v2/docs/MIGRATION_TRACKER.md`

## Current Feature Inventory Summary

Codex identified these old Sam features:

1. Core assistant runtime loop
2. Daemon API + dashboard backend
3. React dashboard shell
4. Voice capture
5. Desktop launcher/orb shell
6. Intent system + capability registry
7. Memory subsystem
8. Vault/SQLite persistence
9. Task/goal/pipeline workflows
10. System watchers + presence engine
11. WhatsApp automation suite
12. Comms channels
13. Tooling agents
14. Authority/approval governance

## First Migration Target

Start with:

`Vault/SQLite persistence`

Why:

This is the foundation for future memory, tasks, goals, approvals, audit logs, run logs, and workflows.

Old reference files:

- `vault/schema.py`
- `daemon/vault_routes.py`
- `goals/tracker.py`
- `pipeline/engine.py`

Destination:

```text
sam_v2/storage/
  __init__.py
  schema.py
  db.py
  models.py

sam_v2/tests_live/test_vault_live.py
```

## First Migration Requirements

Implement only a minimal storage foundation.

Do:

- initialize SQLite DB
- create required Sam v2 tables
- support audit/event storage
- support a simple task-like record if clean
- catch failures clearly
- align with `sam_v2/diagnostics/result.py` and `sam_v2/diagnostics/error_types.py` where practical
- provide a live test script using a real temporary SQLite DB

Do not:

- migrate daemon routes yet
- migrate goals yet
- migrate pipeline yet
- delete old files yet
- refactor unrelated old Sam files

## Live Testing Rule

The live test must run real SQLite operations.

Command target:

```bash
python sam_v2/tests_live/test_vault_live.py
```

Expected test behavior:

1. create temporary SQLite DB
2. initialize schema
3. insert audit/event row
4. read audit/event row
5. insert task-like row if implemented
6. read task-like row
7. print clear pass/fail output

## After First Migration

If vault storage passes live test:

- update `sam_v2/docs/MIGRATION_TRACKER.md`
- set `Vault/SQLite persistence` to `Done`
- recommend the next migration feature

Recommended next candidates after storage:

1. Daemon core skeleton: health/chat/ws only
2. Memory subsystem
3. Authority/approval governance

## Error Handling Foundation

Every migrated feature should move toward this result shape:

```text
status: success | failed | partial | blocked | needs_approval
summary: human-readable explanation
error_type: optional structured category
error_message: optional details
next_action: retry | ask_user | escalate_worker | stop | request_approval
metadata: optional details
```

## Approval Boundaries

Sam must request approval before:

- pushing to remote
- deploying
- deleting old files
- sending messages/emails
- editing secrets/env files
- changing production config
- spending money
- publishing public content

## Current Next Step

Codex should now execute the first migration:

`Vault/SQLite persistence -> sam_v2/storage/`
