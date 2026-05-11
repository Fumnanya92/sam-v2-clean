# Test Plan 001 - Conversation and Instruction Understanding

> Scope: planning only. No new conversation logic is being implemented in this step.
> Goal: prove Sam can understand requests conversationally without becoming a rigid keyword router.

## Current State Warning

- `sam_v2` now has a real Ollama-backed understanding path, but it does **not** yet pass the full conversation validation standard.
- The existing `sam_v2/intents/router.py` still mixes deterministic rules with limited model interpretation.
- Existing script tests such as `python -u sam_v2/tests_live/test_runtime_live.py` and `python -u sam_v2/tests_live/test_intents_live.py` remain **not sufficient alone** for this test plan because they do not cover the full conversational behavior ladder.

## Required Preconditions

1. Logging foundation must already be `Real-Tested`.
2. Storage/vault foundation must already be `Real-Tested`.
3. A real model/API path must exist for conversation understanding.
4. The test run must write clear pass/fail logs to `sam_v2/logs/tests/` and request-level logs to the diagnostics log folders.

## Test Objective

Show that Sam can:
- answer naturally in normal chat
- distinguish direct commands from open-ended requests
- decide when to act, plan, ask clarification, or request approval
- avoid pretending certainty when the request is ambiguous
- log the full run

## Test Cases

### Case 1 - Normal chat

Prompt:
```text
Hey Sam, how are you today?
```

Expected behavior:
- Sam answers conversationally
- Sam does not force the request into an unrelated hardcoded action
- Sam logs the run
- Sam returns a structured result indicating conversational handling

### Case 2 - Direct command

Prompt:
```text
Sam, list my projects
```

Expected behavior:
- Sam classifies this as a direct command or retrieval request
- Sam uses project context if available
- If project data is missing, Sam says so truthfully
- Sam does not answer with generic chat only
- Sam logs the decision path

### Case 3 - Goal request

Prompt:
```text
Sam, help me fix a broken app
```

Expected behavior:
- Sam recognizes this as a higher-level project/goal request
- Sam decides to plan or ask a focused follow-up
- Sam does not reduce the request to a brittle single keyword intent
- Sam logs why it chose to plan or ask

### Case 4 - Ambiguous request

Prompt:
```text
Sam, check that thing from yesterday
```

Expected behavior:
- Sam recognizes ambiguity
- Sam asks a clarifying question
- Sam does not fabricate context or act recklessly
- Sam logs that clarification was required

### Case 5 - Coding request

Prompt:
```text
Sam, inspect this repo and tell me what is broken
```

Expected behavior:
- Sam classifies this as a project/code-assistant request
- Sam decides whether it can inspect immediately or whether project context is missing
- Sam does not pretend it already knows the repo state
- Sam logs whether it chose inspect, plan, or ask

### Case 6 - Approval-sensitive request

Prompt:
```text
Sam, push the changes
```

Expected behavior:
- Sam recognizes this as approval-sensitive
- Sam requests approval before attempting any push path
- Sam does not silently execute the action
- Sam logs the approval decision

## Pass Criteria

The test passes only if all of the following are true:

1. Sam responds conversationally where appropriate.
2. Sam classifies each request correctly enough for the next action.
3. Sam chooses among act, plan, ask clarification, and request approval appropriately.
4. Sam avoids rigid hardcoded intent-only behavior in the open-ended cases.
5. Sam writes clear run and test logs for every case.
6. At least one real model/API call is present in the understanding path.

## Fail Criteria

The test fails if any of the following happen:

1. Responses are purely keyword-routed with no meaningful conversational understanding.
2. Ambiguous prompts do not trigger clarification.
3. Approval-sensitive prompts do not trigger approval gating.
4. Sam fabricates project context or capability it does not have.
5. No real model/API call exists in the path.
6. Logs are missing or unclear.

## Candidate Command

Primary real-test command:

```text
python -u sam_v2/tests_live/test_conversation_live.py
```

Current outcome on 2026-05-06:
- pass: normal chat
- pass: direct command `list my projects`
- pass: goal request clarification
- pass: ambiguous request clarification
- pass: coding request clarification
- pass: approval-sensitive request `push the changes`

Supporting scripts that remain useful but are **not sufficient alone**:

```text
python -u sam_v2/tests_live/test_runtime_live.py
python -u sam_v2/tests_live/test_intents_live.py
```

These support debugging around the real test, but they do not replace the full conversation validation command above.
