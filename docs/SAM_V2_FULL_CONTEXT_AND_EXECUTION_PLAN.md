# Sam v2 Full Context and Execution Plan

This document captures the full rebuild direction agreed in chat so the project does not lose context.

## 1. What Sam Is Supposed To Become

Sam is not just a chatbot.

Sam should become a local autonomous assistant that lives on the user's machine and helps the user run real work with minimal input.

The target direction is closer to:

- Friday / Jarvis-style personal assistant
- local chief-of-staff assistant
- project supervisor
- coding/task execution coordinator
- meeting assistant
- business operations assistant
- tool-using agent that can plan, execute, test, report, and ask for approval

Sam should eventually support real workflows such as:

```text
User: Sam, something is breaking in AccessCode NG. Check it, fix it, test it, and show me before pushing.
```

Sam should then:

1. identify the correct project
2. inspect the repo/app state
3. understand the issue
4. create a plan
5. choose the right worker/tool
6. make or supervise code changes
7. run live tests/builds
8. inspect failures
9. iterate safely
10. summarize what changed
11. ask approval before pushing/deploying

## 2. Working Agreement

We are not throwing old Sam away blindly.

Old Sam is source/reference material.

`sam_v2/` is the clean destination.

Migration flow:

1. Pick one feature from old Sam.
2. Inspect old files and dependencies.
3. Decide whether to copy, rewrite, simplify, or drop.
4. Move/rebuild useful parts into `sam_v2/`.
5. Run live tests.
6. Update tracker/logs.
7. Only after success and user approval, archive/delete old files.

## 3. Roles

### ChatGPT

Responsible for:

- planning
- architecture
- migration decisions
- reviewing Codex outputs
- writing Codex execution briefs
- tracking progress
- keeping the long-term product vision in mind

### Codex

Responsible for:

- inspecting actual repo files
- editing code
- running commands/tests locally
- migrating features
- committing changes
- reporting results

## 4. Non-Negotiable Rules

1. Migrate one feature at a time.
2. Do not mix many features in one migration.
3. Do not delete old files until the new version works and the user approves.
4. Every migrated feature must be live tested.
5. Mock tests alone do not count as completion.
6. Sam must catch and report its own errors.
7. Every action should produce logs or visible failure output.
8. Sensitive actions require approval.
9. Keep new code inside `sam_v2/` unless there is a clear reason not to.
10. Codex must always report files changed, test command, result, failures, tracker status, and recommended next feature.

## 5. Why The Old Build Was Failing

The old project appears to have too many responsibilities mixed together:

- runtime loop mixed with UI/state
- daemon and assistant loop overlap
- `agent/` and `agents/` both contain execution concepts
- large intent dispatcher with broad side effects
- multiple daemon route layers and aliases/stubs
- UI pages that depend on incomplete backend parity
- background watchers that may swallow exceptions
- missing consistent logs/error handling

This rebuild must avoid repeating that structure.

## 6. Assistant OS Architecture Direction

Sam should be structured like an assistant operating system:

```text
sam_v2/
  core/
  supervisor/
  planner/
  workers/
  tools/
  projects/
  storage/
  memory/
  approvals/
  capabilities/
  upgrades/
  meetings/
  browser/
  vision/
  diagnostics/
  tests_live/
  docs/
```

This does not mean all folders must be created at once.

It means every migration decision should support this direction.

## 7. Sam As Supervisor, Not Always The Coder

Sam should not always be the coding model.

Sam should supervise and delegate.

Possible worker types:

- local LLM worker
- coding agent worker
- terminal worker
- Git worker
- browser worker
- vision worker
- meeting/transcription worker
- memory worker

Sam should decide:

```text
What is the task?
Which project does it affect?
Which worker/tool is best?
What permissions are required?
How do we verify success?
```

## 8. Local Machine Vision

Sam lives on the user's machine.

The user works on multiple projects and may be away from the system while the machine is on.

Sam should eventually be reachable remotely and able to work locally.

Example desired flow:

```text
User: Sam, client wants member positions added to the attendance app. Add it, test it, and show me before pushing.
```

Sam should:

1. locate the attendance app project
2. check Git state
3. create a safe branch
4. inspect current models/screens/services
5. design the feature
6. delegate code edits if needed
7. run real tests/builds
8. iterate on errors
9. summarize changes
10. ask approval before pushing

## 9. Project Awareness Requirement

Sam should eventually know the user's projects:

- AccessCode NG
- BulkBay
- FocusFlow
- Guest Welcome Attendance App
- Sam-agent
- other local projects

For each project Sam should know:

- repo path
- stack
- test command
- build command
- deployment method
- risk level
- active branch
- important files

## 10. Permission Model

Sam can be powerful, but not reckless.

Permission levels:

- `suggest_only`: Sam can advise but not act
- `draft`: Sam can prepare output for review
- `execute_safe`: Sam can run safe/low-risk actions
- `approval_required`: Sam must ask before continuing
- `blocked`: Sam must not perform this action

Approval required for:

- pushing to remote
- deploying
- deleting files
- editing secrets/env files
- sending emails/messages
- spending money
- publishing public content
- changing production config
- enabling a new self-upgrade

## 11. Capability Awareness

Sam must know what it can and cannot do.

When a task requires a missing capability, Sam should not pretend.

Expected behavior:

```text
I cannot complete this yet because my system does not currently have [missing capability/tool/permission].

I can add this capability if you approve.
```

Sam should check:

- Do I have the capability?
- Do I have the tool/worker installed?
- Do I have access to the project/files/account?
- Do I have permission?
- Do I need approval?

## 12. Controlled Self-Improvement

Sam should eventually improve itself, but only through controlled upgrade flow.

Flow:

1. detect missing capability
2. propose upgrade
3. ask approval
4. create branch
5. implement through worker/Codex/coding agent
6. run tests/live test
7. log everything
8. ask approval before enabling/merging

No uncontrolled self-modifying code.

## 13. Error Catching Is Core

Sam must catch its own errors.

Every major action should return structured results:

```text
status: success | failed | partial | blocked | needs_approval
summary: human-readable explanation
error_type: optional structured category
error_message: optional details
next_action: retry | ask_user | escalate_worker | stop | request_approval
metadata: optional details
```

Error categories should include:

- missing_capability
- missing_permission
- tool_failed
- command_failed
- test_failed
- file_access_error
- git_error
- model_error
- browser_error
- timeout
- unknown_error

Sam should decide the next safe action:

- retry once if safe
- collect logs
- ask user for missing information
- request permission
- escalate to stronger worker
- stop if risky or repeated failure

## 14. Logging Requirement

Everything Sam does must be traceable.

Logs should capture:

- received request
- interpreted intent
- selected project
- plan created
- tools/workers selected
- permission checks
- commands run
- files changed
- browser actions
- test results
- errors/failures
- retries
- approval requests
- final summary

Suggested structure:

```text
sam_v2/logs/
  runs/
  actions/
  errors/
  summaries/
```

## 15. Live Testing Requirement

Live testing matters more than mocks for Sam's agent behavior.

A feature is not done until it works in a real environment or realistic local workflow.

Examples:

- storage feature: real SQLite DB test
- terminal worker: real command execution
- git worker: real repo state inspection
- browser worker: real browser/page action
- coding workflow: real project edit + real build/test command

## 16. Meeting Assistant Vision

Sam should eventually support meeting assistant workflows:

- join/listen where permitted
- transcribe conversation
- identify speakers where possible
- summarize discussion
- list decisions
- list action items
- assign owners
- produce clean meeting minutes
- save notes to workspace/memory
- draft/send minutes with approval

## 17. Feature Inventory Source

Codex created/moved feature inventory into docs.

Use:

```text
sam_v2/docs/FEATURE_INVENTORY.md
sam_v2/docs/MIGRATION_TRACKER.md
```

Detected major old Sam features:

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

## 18. GitHub Issues Created For Tracking

These issues were created to preserve the architecture and migration decisions:

- Issue #3: Sam v2 rebuild: create clean workspace and migrate features one by one
- Issue #4: Sam v2 architecture: supervisor agent for coding, testing, and project execution
- Issue #5: Sam v2 capability system: meeting notes, self-awareness, self-improvement, and logs
- Issue #6: Sam v2 foundation: self-error catching, recovery, and run reporting
- Issue #7: Sam v2 workflow: plan here, execute in Codex, migrate from old files one feature at a time
- Issue #8: Sam v2 next migration: vault schema and minimal data access layer

## 19. Current Migration Target

Start with:

```text
Vault/SQLite persistence
```

Why first:

Storage is foundation for:

- tasks
- goals
- memory
- approvals
- audit logs
- run logs
- workflows
- dashboard state

Old files to inspect:

```text
vault/schema.py
daemon/vault_routes.py
goals/tracker.py
pipeline/engine.py
```

New destination:

```text
sam_v2/storage/__init__.py
sam_v2/storage/schema.py
sam_v2/storage/db.py
sam_v2/storage/models.py
sam_v2/tests_live/test_vault_live.py
```

## 20. Codex Execution Rule Going Forward

Codex should always:

1. read this document first
2. read feature inventory and tracker
3. work on only the assigned feature
4. create/update files under `sam_v2/`
5. run live test
6. update tracker
7. commit with focused message
8. report result clearly

## 21. Immediate Codex Task

Execute Issue #8.

Migrate Vault/SQLite persistence into `sam_v2/storage/`.

Do not migrate daemon routes, goals, pipeline, or memory yet.

Do not delete old files yet.
