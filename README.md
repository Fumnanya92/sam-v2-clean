# Sam v2

Sam v2 is the clean rebuild workspace for Sam.

Old Sam files remain in the repository only as reference material while features are migrated one by one.

## Current Goal

Build Sam as an autonomous assistant operating system that can:

- understand user goals
- plan tasks
- use controlled tools/workers
- run live tests
- catch and report its own errors
- ask for approval before sensitive actions
- keep logs of everything it does

## First Foundation

This first version focuses on:

- structured results
- error categories
- run logging
- migration tracking
- live testing rules
- local assistant access model

No old feature is considered migrated until it is rebuilt or safely copied into `sam_v2/`, live tested, and marked in the tracker.

## Run Sam v2

Native desktop shell:

```bash
python -m sam_v2
```

One-shot request:

```bash
python -m sam_v2 --once "what can you do"
```

Interactive REPL:

```bash
python -m sam_v2 --cli
```

Daemon mode:

```bash
python -m sam_v2 --daemon
```

You can still launch the native shell explicitly with:

```bash
python -m sam_v2 --native-ui
```
