"""
Block 2: Sam's simple router.

ONE decision point per message:
  chat    — conversation, questions, expressing thoughts, anything that isn't
             a clear directive to take action right now
  find    — the user is explicitly directing Sam to search for a file/project
  code    — the user is explicitly directing Sam to write/fix/edit code
  run     — the user is explicitly directing Sam to execute something
  remember — the user is explicitly asking Sam to save/store something

KEY PRINCIPLE: When in doubt, route to chat.

The hardest case is action words used in conversation:
  "I can't wait to test you"       → chat  (expressing excitement, not ordering a test)
  "run the tests"                  → run   (clear directive)
  "I just found the bug"           → chat  (describing past event, not asking to find)
  "find the planflow project"      → find  (clear directive)
  "I added a feature, can't wait to run it"  → chat  (telling Sam something, not ordering a run)

The LLM makes ALL routing decisions. No keyword shortcuts. Words alone don't
tell you what someone means — context and intent do. Only the LLM can judge that.
"""

from __future__ import annotations

import json as _json
from urllib import request as _req
from typing import Any, Literal

RouteDecision = Literal["chat", "find", "code", "run", "remember", "query"]

_ROUTE_PROMPT = """\
You are Sam's routing layer. Classify the user message into ONE category.

Categories:
  chat     — talking, asking questions, sharing news, expressing feelings, greeting,
             brainstorming, or using action words without actually ordering Sam to act
  find     — telling Sam to search the machine for a file, folder, or project
  code     — telling Sam to write, fix, edit, or review code
  run      — telling Sam to execute a command, run tests, or start an app
  remember — telling Sam to SAVE or STORE a new piece of information (not to recall it)
  query    — telling Sam to READ data from a database, Firestore, or external service
             (checking records, fetching due dates, looking up a document, reading a collection)

THE KEY DISTINCTION — action word vs. actual directive:
  "I can't wait to test you"                → chat  (expressing excitement, not ordering)
  "run the tests"                           → run   (clear directive to Sam)
  "I can't wait to run a task on you"       → chat  (expressing anticipation, not ordering)
  "run the app now"                         → run   (clear directive)
  "I just found the bug"                    → chat  (sharing news, not asking Sam to find)
  "find the planflow project"               → find  (telling Sam to search)
  "how do I run flutter apps?"              → chat  (question about how to do it, not ordering)
  "fix the login bug in the estate app"     → code  (telling Sam to fix code)
  "can you fix code in general?"            → chat  (asking about capability, not ordering)
  "remember that my key is in Documents"    → remember (telling Sam to store NEW info)
  "Sam, search my Desktop for planflow"     → find  (clear directive)
  "I added a feature and want to test it"   → chat  (telling Sam about their own plan)
  "check when levies are due in the estate app"     → query  (reading data from a database)
  "check the residents Firestore collection"        → query  (reading live data)
  "what does the Firestore doc say about payments"  → query  (reading data)
  "do you still have my firebase key"               → chat  (asking if Sam has it, not storing)
  "what was the value I asked you to hold"          → chat  (recalling from conversation, not storing)
  "what did I tell you earlier"                     → chat  (recalling conversation context)
  "do you remember what I said"                     → chat  (recalling, not storing)
  "check your memory you saved it"                  → chat  (asking Sam to recall, not to store new info)
  "you have it already"                             → chat  (user asserting Sam already has the info)
  "you saved it earlier"                            → chat  (telling Sam to look it up, not to store)
  "it's in your memory"                             → chat  (directing Sam to recall, not store)
  "you aren't getting me"                           → chat  (frustration/observation, never storing)
  "why aren't you doing anything"                   → chat  (expressing frustration, not a directive)
  "you should know this already"                    → chat  (expressing expectation, not storing)

RULE: If the user is describing, wondering, asking, or expressing — it is chat.
RULE: If the user is directing Sam to DO something specific right now — it is the matching action.
RULE: query = reading live data from a database or external service, not reading local files.
RULE: remember = STORING new information only. Asking "what did I tell you" or "what's the value" = chat (recall from history).
RULE: If you are not sure — choose chat.

Reply with EXACTLY one word: chat, find, code, run, remember, or query
No explanation. No punctuation. Just the word.

User message: {message}"""


def route(text: str, llm_client: Any, history: list[dict] | None = None) -> RouteDecision:
    """
    Ask the LLM to decide what kind of request this is.

    Returns one of: chat | find | code | run | remember

    The LLM makes all decisions — no keyword shortcuts. This prevents the
    classic false-positive where action words in conversation get treated as
    directives (e.g. "I can't wait to run a task on you" being routed to run).
    """
    # Build conversation context from recent history so the LLM understands follow-ups
    context_block = ""
    if history:
        recent = history[-6:]  # Last 3 exchanges
        lines = []
        for m in recent:
            role = "User" if m["role"] == "user" else "Sam"
            lines.append(f"{role}: {m['content'][:120]}")
        context_block = "\nRecent conversation:\n" + "\n".join(lines) + "\n"

    prompt = _ROUTE_PROMPT.format(message=text.strip()) + context_block

    try:
        payload = _json.dumps({
            "model": llm_client.resolve_model(),
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = _req.Request(
            f"{llm_client.settings.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _req.urlopen(req, timeout=10) as resp:
            body = _json.loads(resp.read())
            raw = str(body.get("response", "")).strip().lower()
            # Take just the first word in case the LLM added anything extra
            word = raw.split()[0] if raw.split() else ""

        if word in {"chat", "find", "code", "run", "remember", "query"}:
            return word  # type: ignore[return-value]

    except Exception:
        pass

    # Default: chat. Safe fallback — never accidentally trigger an action.
    return "chat"
