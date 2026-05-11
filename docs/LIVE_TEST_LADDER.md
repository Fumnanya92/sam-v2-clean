# Sam v2 Live-Test Ladder

> Purpose: validate Sam from the lowest-risk foundation upward.
> Rule: do not move to the next layer until the current layer has a documented real pass/fail result.

## Validation Order

1. Diagnostics and logging foundation
- Prove Sam writes real run, action, error, summary, and test logs.
- Output required: saved log files plus pass/fail transcript.

2. Storage and vault foundation
- Prove real SQLite schema creation, audit storage, and task storage.
- Output required: DB path, inserted IDs, retrieval proof, failure-path proof.

3. Conversation and instruction understanding
- Prove Sam can understand natural requests without collapsing into rigid keyword-only routing.
- Output required: real model/API call logs, classification result, decision result, run logs.

4. Memory and session
- Prove temporary memory, persistent memory, and session state work through a real request path.
- Output required: memory file diff, session file diff, audit trail.

5. Safe terminal and file tools
- Prove Sam can inspect files/folders and run safe local commands on real local paths.
- Output required: command logs, file outputs, failure handling, approval behavior when needed.

6. Project registry and git inspection
- Prove Sam can identify a real local project, inspect repo state, and report the active branch/test/build commands.
- Output required: project record, git status/branch evidence, structured summary.

7. Code-fix workflow
- Prove Sam can inspect a real broken repo, identify a problem, make a small safe fix, rerun tests, and summarize the diff.
- Output required: before/after test result, diff summary, worker logs, approval logs if needed.

8. Approval system
- Prove approval gating works in real sensitive flows such as push, delete, or governed command execution.
- Output required: pending approval record, approval action, resumed execution or block.

9. Workflows, tasks, and goals
- Prove Sam can create tasks/goals, run a multi-step workflow, pause, resume, and report completion.
- Output required: storage records, workflow logs, user-facing summary.

10. Browser, vision, and external integrations
- Prove one external integration at a time with real accounts or devices in controlled conditions.
- Output required: integration-specific logs, safety confirmation, clear rollback path.

## Current Recommended Starting Point

1. Diagnostics and logging foundation
2. Storage and vault foundation
3. Conversation and instruction understanding

## Stop Conditions

- If a feature depends on a lower layer that is only `Migrated But Unverified`, stop and validate the lower layer first.
- If a test uses mocks/fake objects where the feature really depends on model calls, repos, commands, or external systems, do not promote the feature to `Real-Tested`.
- If the result is partial, update the tracker honestly before any further migration work.
