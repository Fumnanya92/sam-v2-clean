# Sam v2

Sam v2 is a clean rebuild of Sam, my personal AI assistant.

The vision is to build a practical assistant that can help with real tasks on my computer, support my development workflow, and grow into a reliable system I can use every day.

Sam should feel simple to use, easy to improve, and safe enough to trust with important work.

## Vision

Sam v2 is being built to:

- understand what I want to do
- help plan and complete tasks
- use tools and workers when needed
- run checks and tests where possible
- explain what happened clearly
- keep useful logs and history
- support local-first execution where it makes sense
- use LLMs wisely instead of depending on them for everything

The long-term goal is to have an assistant that can move from conversation to action without becoming messy, expensive, or hard to maintain.

## Current Focus

The first focus is the foundation:

- a working Python package
- command-line usage
- one-shot requests
- daemon/API mode
- local runtime structure
- logging and diagnostics
- simple tool execution
- migration tracking

Once the foundation is stable, features will be added gradually.

## Running Sam

Native desktop shell:

```bash
python -m sam_v2
```

One-shot request:

```bash
python -m sam_v2 --once "what can you do"
```

Interactive CLI:

```bash
python -m sam_v2 --cli
```

Daemon mode:

```bash
python -m sam_v2 --daemon
```

Native UI mode:

```bash
python -m sam_v2 --native-ui
```

## Project Direction

Sam v2 will grow step by step.

The idea is to keep the system understandable while still making it powerful. Each major feature should be easy to test, easy to debug, and easy to improve later.

Planned areas include:

- assistant core
- memory
- local tools
- browser and desktop automation
- developer workflow support
- API/daemon mode
- native desktop interface
- controlled task execution
- approval flow for sensitive actions
- future integrations

## Repository Status

This repository is now the main workspace for Sam v2.

The README will keep changing as the project grows.
