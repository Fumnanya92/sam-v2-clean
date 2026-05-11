# Sam v2 Feature Inventory

> Generated from repo inspection on 2026-05-06.
> Branch inspected: `rebuild/sam-clean-v2`.

## A) Project Overview

### Detected stack
- Backend/runtime: Python 3 (asyncio, FastAPI, websockets, aiosqlite, psutil, pyautogui, pywebview)
- Frontend/UI: React 19 + TypeScript + Vite (`ui/`)
- Persistence: JSON memory files + SQLite vault mirror (`vault/schema.py`, `memory/memory_manager.py`)
- Realtime: WebSocket channels for speech and dashboard events

### Main languages/frameworks
- Python, TypeScript, CSS, Markdown
- FastAPI/uvicorn, React/Vite, sqlite (aiosqlite)

### Likely app entry points
- `main.py` (primary assistant runtime loop)
- `daemon/main.py` (FastAPI daemon + WebSocket integration)
- `launcher.py` and `start_launcher.bat` (desktop launcher)
- `ui/src/main.tsx` (React dashboard entry)
- `orb/main.py` and `start_orb.py` (orb UI path)

## B) Old Sam Structure (Folder-by-folder)

- `actions/`: user-triggered capabilities (system control, browser, media, reminders, code tools, etc.)
- `agent/`: older task execution framework (planner/executor/monitor/task queue)
- `agents/`: newer multi-agent and delegation system (orchestrator, code_surgeon, test_runner, tool_forge)
- `assistant/`: assistant helper flows (morning briefing, message reader, daily planner shim)
- `authority/`: approval/governance/audit framework
- `automation/`: WhatsApp and browser automation engines/controllers
- `comms/`: Telegram/Discord channel adapters and manager
- `config/`: YAML/json config and key templates
- `core/`: main system prompt + capability registry
- `daemon/`: FastAPI routes, ws service, daemon lifecycle, API aliases/stubs
- `docs/`: architecture notes, cleanup notes, implementation docs
- `goals/`: goal tracker logic backed by vault
- `intents/`: intent routing and action handlers (large central dispatcher)
- `llm/` + `llm.py`: model manager/provider calling surface
- `memory/`: JSON memory, temporary/session memory, config access, project index
- `orb/`: orb visual shell/animations/position manager
- `personality/`: adaptive personality learner persisted to vault
- `pipeline/`: content pipeline engine
- `roles/`: role YAML definitions (exec + specialist personas)
- `scripts/`: utility/bootstrap scripts (browser/debug helpers)
- `skills/`: local skills + very large imported `antigravity_skills` subtree (reference/vendor-like)
- `system/`: watcher/event bus/presence/notifier/monitoring infrastructure
- `tests/`: integration and capability tests
- `tasks/`: plans, live test scripts, migration notes
- `ui/`: React dashboard (pages/components/hooks/styles)
- `vault/`: SQLite schema and persistence contract
- `workflows/`: workflow engine

## C) Detected Features

## 1. Core assistant runtime loop
- Old files involved: `main.py`, `conversation_state.py`, `llm.py`, `tts.py`, `ui.py`, `intents/handlers.py`
- Appears to do: starts Sam loop, captures user input, routes intents, generates responses, drives TTS/UI state
- Dependencies: dotenv, internal actions/intents modules, speech websocket bridge
- Migration difficulty: High
- Confidence: High
- Recommended action: Rewrite
- Suggested live test: start runtime and verify one end-to-end prompt (input -> intent -> spoken + UI output)

## 2. Daemon API + dashboard backend
- Old files involved: `daemon/main.py`, `daemon/api_routes.py`, `daemon/vault_routes.py`, `daemon/missing_routes.py`, `daemon/extra_routes.py`, `daemon/ws_service.py`
- Appears to do: serves REST + WS, bridges chat queue, broadcasts events to dashboard, hosts compatibility routes
- Dependencies: FastAPI, aiosqlite, vault schema, main ai_loop import path
- Migration difficulty: High
- Confidence: High
- Recommended action: Simplify
- Suggested live test: boot daemon and hit `/health`, `/api/chat`, `/ws` roundtrip

## 3. React dashboard shell
- Old files involved: `ui/src/App.tsx`, `ui/src/hooks/useWebSocket.ts`, `ui/src/hooks/useApi.ts`, `ui/src/pages/*`, `ui/src/components/*`
- Appears to do: multi-page dashboard for chat, tasks, goals, memory, workflows, settings, authority, sites, etc.
- Dependencies: React, Vite, websocket backend, multiple API route groups
- Migration difficulty: High
- Confidence: High
- Recommended action: Simplify
- Suggested live test: run UI dev server and verify navigation + chat panel websocket updates

## 4. Voice capture (Web Speech + websocket)
- Old files involved: `speech_to_text_websocket.py`, `websocket_server.py`, `speech_client.html`
- Appears to do: browser/webview speech capture, wake-word mode, transcript queueing to runtime
- Dependencies: websockets, pywebview/browser, local HTTP serving
- Migration difficulty: Medium
- Confidence: High
- Recommended action: Rewrite
- Suggested live test: wake word + final transcript reaches runtime queue

## 5. Desktop launcher/orb shell
- Old files involved: `launcher.py`, `start_launcher.bat`, `start_launcher.vbs`, `orb/main.py`, `start_orb.py`, `orb/*`
- Appears to do: desktop floating launcher/orb and process bootstrap
- Dependencies: tkinter / graphics stack, subprocess/process state
- Migration difficulty: Medium
- Confidence: Medium
- Recommended action: Inspect more
- Suggested live test: launcher click starts one Sam process and updates running indicator

## 6. Intent system + capability registry
- Old files involved: `core/prompt.txt`, `core/capabilities.py`, `intents/__init__.py`, `intents/handlers.py`
- Appears to do: intent extraction contract and action dispatch with large handler registry
- Dependencies: most action modules, temp memory, ui hooks
- Migration difficulty: High
- Confidence: High
- Recommended action: Rewrite
- Suggested live test: validate top intents (`chat`, `open_app`, `run_command`, `set_reminder`) map correctly

## 7. Memory subsystem (JSON + session)
- Old files involved: `memory/memory_manager.py`, `memory/temporary_memory.py`, `memory/session_state.py`, `memory/memory.json`
- Appears to do: persistent memory merge/load/save + session parameter state
- Dependencies: JSON files, optional SQLite mirror
- Migration difficulty: Medium
- Confidence: High
- Recommended action: Copy
- Suggested live test: update memory key and verify persisted + reload

## 8. Vault/SQLite persistence
- Old files involved: `vault/schema.py`, `daemon/vault_routes.py`, `goals/tracker.py`, `pipeline/engine.py`
- Appears to do: durable storage for tasks/goals/conversations/content and related APIs
- Dependencies: aiosqlite, DB path + migrations
- Migration difficulty: Medium
- Confidence: High
- Recommended action: Copy
- Suggested live test: initialize DB and create/read one row per core table

## 9. Task/goal/pipeline workflow features
- Old files involved: `goals/tracker.py`, `pipeline/engine.py`, related API routes in `daemon/api_routes.py`
- Appears to do: planning/goal scoring/content pipeline CRUD
- Dependencies: vault schema, WS events, dashboard components
- Migration difficulty: Medium
- Confidence: Medium
- Recommended action: Simplify
- Suggested live test: create goal and pipeline draft from API then verify UI reflection

## 10. System watchers + presence engine
- Old files involved: `system/presence_engine.py`, `system/system_watcher.py`, `system/watchers/*`, `system/event_bus.py`, `system/task_queue.py`
- Appears to do: background monitoring, proactive suggestions, file/calendar/system events
- Dependencies: psutil, keyboard, project index, event bus
- Migration difficulty: High
- Confidence: Medium
- Recommended action: Inspect more
- Suggested live test: trigger file change event and verify task/test notification path

## 11. WhatsApp automation suite
- Old files involved: `automation/whatsapp_*`, `automation/reply_*`, `assistant/message_reader.py`, WhatsApp-related intent handlers/tests
- Appears to do: read/summarize/draft/reply flows for WhatsApp via UI automation/DOM helpers
- Dependencies: pyautogui, clipboard, browser/debug state, OCR fallbacks
- Migration difficulty: High
- Confidence: High
- Recommended action: Rewrite
- Suggested live test: controlled test account flow from fetch unread -> draft -> confirm send

## 12. Comms channels (Telegram/Discord)
- Old files involved: `comms/manager.py`, `comms/channels/telegram.py`, `comms/channels/discord.py`
- Appears to do: optional external message adapters into same chat queue
- Dependencies: channel tokens/libs, daemon queue
- Migration difficulty: Medium
- Confidence: Medium
- Recommended action: Inspect more
- Suggested live test: send inbound test message from one channel to queue + response

## 13. Tooling agents (code/test/dev)
- Old files involved: `agents/code_surgeon.py`, `agents/test_runner.py`, `agents/tool_forge.py`, `actions/dev_agent.py`, `actions/code_helper.py`
- Appears to do: code generation/debug/test assistance and task delegation
- Dependencies: llm manager, project index, shell/file actions
- Migration difficulty: High
- Confidence: Medium
- Recommended action: Simplify
- Suggested live test: run a constrained “fix failing test” cycle in sandbox project

## 14. Authority/approval governance
- Old files involved: `authority/engine.py`, `authority/approval.py`, `authority/audit.py`, authority API aliases in daemon routes
- Appears to do: policy decisioning, approval queue, audit trail
- Dependencies: vault DB, API route integration
- Migration difficulty: Medium
- Confidence: Medium
- Recommended action: Copy
- Suggested live test: submit approval-required action and verify pending/approve/deny audit entries

## D) Suspected Problem Areas

- Clashing responsibilities:
  - `main.py` and `daemon/main.py` can both own loop responsibilities depending on embedded mode.
  - Both `agent/` and `agents/` contain orchestration/execution concepts.
- Duplicated logic:
  - Multiple API route layers include aliases/stubs (`api_routes`, `missing_routes`, `extra_routes`, `vault_routes`).
  - Legacy + new UI theme stacks coexist (`chat.css`, `chat-brutalist.css`, `brutalist-overrides.css`, legacy comments).
- Copied/unused UI areas:
  - Many pages/routes are present; some backend endpoints are stubbed placeholders (`/api/sites/*` file/git responses).
  - Legacy route/style markers indicate partial migrations.
- Fragile code:
  - Large monolithic intent handler file (`intents/handlers.py`) with broad side effects.
  - Runtime relies on optional/background watchers that swallow exceptions.
- Hardcoded paths/secrets risk:
  - `memory/project_index.json` includes absolute user paths and git remotes.
  - local `.env` present and `config/api_keys.json` write path exists in code.
- Missing logs/error handling:
  - Several broad `except Exception: pass` startup blocks in `main.py` and daemon startup paths.

## E) Recommended First 3 Features To Migrate

1. Vault schema + minimal data access layer
- Why first: stable foundation for tasks/conversations/goals and other modules.

2. Daemon core skeleton (health/chat/ws only)
- Why second: gives a clean API/runtime spine for Sam v2 and UI integration.

3. Memory subsystem (temporary + persistent JSON abstraction)
- Why third: needed by intents/runtime while still relatively isolated and testable.

Most recommended first feature: **Vault schema + minimal data access layer**.
