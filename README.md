# Sam v2 Clean

Sam v2 Clean is the new working home for Sam.

The goal is not to dump every old feature back into the assistant. The goal is to rebuild Sam into a stable, testable, Python-first assistant that can grow without breaking itself.

## What Sam Should Be

Sam is a personal AI assistant built to help with real work:

- understand a user request
- decide the safest next action
- use local tools where possible
- call an LLM only when needed
- keep logs of what happened
- report errors clearly
- ask for approval before sensitive actions
- support CLI, daemon, and desktop UI modes

## Current Direction

This repo is now the source of truth for Sam v2.

Old Sam files may exist in the repository as reference material, but they are not automatically trusted. A feature is only considered part of Sam v2 when it has been moved into the clean `sam_v2/` structure, tested, and documented.

## Core Rules

1. Keep it Python-first.
2. Keep it code-first before LLM-first.
3. Do not add ten features at once.
4. Migrate one feature, test it, then move to the next.
5. Every tool should return a structured result.
6. Every failure should be logged clearly.
7. Sensitive actions must require approval.
8. No feature is complete until it runs live.

## Planned Runtime Modes

Sam v2 should support these modes:

### Native desktop shell

```bash
python -m sam_v2
```

### One-shot command

```bash
python -m sam_v2 --once "what can you do"
```

### CLI mode

```bash
python -m sam_v2 --cli
```

### Daemon/API mode

```bash
python -m sam_v2 --daemon
```

### Explicit native UI mode

```bash
python -m sam_v2 --native-ui
```

## Current Status

Sam v2 is in rebuild mode.

The first milestone is to make the foundation work reliably:

- package entrypoint
- config loading
- runtime core
- structured result system
- logging
- CLI mode
- one-shot mode
- daemon health endpoint
- basic tests

After that, features can be migrated one by one.

## Suggested Clean Structure

```text
sam_v2/
  __init__.py
  __main__.py
  config/
  core/
  daemon/
  diagnostics/
  memory/
  tools/
  native_ui/
  tests/
```

Reference or old code should stay outside the clean runtime path until it is reviewed.

## Migration Rule

A migrated feature must have:

- a clear purpose
- a clean module location
- no hidden dependency on old broken code
- structured success/error output
- a simple way to test it
- a note in the migration tracker

## Immediate Priority

Do not start with multi-agent, WhatsApp, dashboard, or heavy automation.

Start with the foundation:

1. `python -m sam_v2 --once "what can you do"`
2. `python -m sam_v2 --cli`
3. `python -m sam_v2 --daemon`
4. health endpoint
5. logging
6. simple tool execution

Once these work, Sam can grow safely.
