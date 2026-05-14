from __future__ import annotations

import tempfile
from pathlib import Path

from core.contextual_resolver import ContextualRequestResolver
from core.runtime import SamRuntime
from core.request_model import IntentRequest
from llm import OllamaIntentOutput
from memory.long_term import (
    MemoryCandidate,
    build_compact_context,
    classify_intent,
    consolidate_memories,
    extract_candidate_memories,
    generate_session_recap,
    get_project_state,
    log_turn,
    memory_debug_report,
    recall_recent_conversation,
    recent_consolidation_log,
    recent_memory_review_log,
    save_memory_candidate,
    search_memories,
    summarize_conversation_if_needed,
    upsert_project_state,
)


def test_memory_extraction_classifies_candidate_types() -> None:
    candidates = extract_candidate_memories(
        source_conversation_id="42",
        user_text="Remember that I prefer concise status updates and Sam should ask before coding.",
        assistant_text="Got it.",
    )

    types = {candidate.memory_type for candidate in candidates}

    assert "semantic" in types
    assert "procedural" in types
    assert all(candidate.importance_score >= 6 for candidate in candidates)


def test_memory_deduplication_merges_similar_memories() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        first = MemoryCandidate(
            content="The user prefers concise status updates.",
            memory_type="semantic",
            importance_score=8,
            confidence_score=0.8,
            source_conversation_id="1",
            tags=["preference"],
        )
        second = MemoryCandidate(
            content="User prefers concise progress updates.",
            memory_type="semantic",
            importance_score=9,
            confidence_score=0.9,
            source_conversation_id="2",
            tags=["preference"],
        )

        first_result, first_id = save_memory_candidate(db_path, first)
        second_result, second_id = save_memory_candidate(db_path, second)
        memories = search_memories(db_path, "concise updates", memory_types=["semantic"])

        assert first_result.ok and second_result.ok
        assert first_id == second_id
        assert len(memories) == 1
        assert memories[0]["importance_score"] == 9


def test_context_retrieval_injects_relevant_and_ignores_irrelevant_memories() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="Sam should ask before coding in the Estate project.",
                memory_type="procedural",
                importance_score=8,
                confidence_score=0.8,
                source_conversation_id="1",
                tags=["procedure"],
            ),
        )
        save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="The user likes jazz playlists for weekend errands.",
                memory_type="semantic",
                importance_score=7,
                confidence_score=0.7,
                source_conversation_id="2",
                tags=["preference"],
            ),
        )

        context = build_compact_context(db_path, session_id="s1", query="Should Sam code in the Estate project?")
        procedural = context["relevant_memories"]["procedural"]
        semantic = context["relevant_memories"]["semantic"]

        assert any("Estate project" in item["content"] for item in procedural)
        assert not any("jazz playlists" in item["content"] for item in semantic)


def test_intent_routing_keeps_planning_chat_out_of_coding() -> None:
    assert classify_intent("I'm thinking through the app architecture before we code") == "architecture"
    assert classify_intent("Let's plan the memory system first") == "planning"
    assert classify_intent("Let's plan then implement the memory system in the repo") == "coding"
    assert classify_intent("Please implement the memory system in the repo files and run tests") == "coding"


def test_contextual_resolver_does_not_delegate_planning_chat_to_active_coding_model() -> None:
    request = IntentRequest(intent="chat", raw_text="Let's plan the memory system first", confidence="high")
    resolved = ContextualRequestResolver().apply(
        text="Let's plan the memory system first",
        request=request,
        memory_block={
            "coding_model": {"value": {"active_coding_model": "codex"}},
            "detected_intent": {"value": "planning"},
        },
    )

    assert resolved.intent == "chat"


def test_contextual_resolver_delegates_only_explicit_coding_with_active_model() -> None:
    request = IntentRequest(intent="chat", raw_text="Please implement the fix in the repo", confidence="high")
    resolved = ContextualRequestResolver().apply(
        text="Please implement the fix in the repo",
        request=request,
        memory_block={
            "coding_model": {"value": {"active_coding_model": "codex"}},
            "detected_intent": {"value": "coding"},
        },
    )

    assert resolved.intent == "autonomous_request"


def test_contextual_resolver_blocks_parser_from_coding_planning_chat() -> None:
    request = IntentRequest(intent="autonomous_request", raw_text="Let's plan the memory system first", confidence="high")
    resolved = ContextualRequestResolver().apply(
        text="Let's plan the memory system first",
        request=request,
        memory_block={"detected_intent": {"value": "planning"}},
    )

    assert resolved.intent == "chat"


def test_compact_context_is_available_before_llm_parse() -> None:
    class _ContextCapturingModel:
        def __init__(self) -> None:
            self.memory_block = {}

        def is_available(self) -> bool:
            return True

        def classify_request(self, user_text: str, *args: object, **kwargs: object) -> OllamaIntentOutput:
            self.memory_block = kwargs.get("memory_block", {})
            return OllamaIntentOutput(intent="chat", parameters={}, response_text="hello", confidence="high", source="test")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        model = _ContextCapturingModel()
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = model
        runtime.handle_text("Let's plan the memory system first")

        assert model.memory_block.get("compact_context", {}).get("value", {}).get("intent") == "planning"
        assert model.memory_block.get("detected_intent", {}).get("value") == "planning"


def test_long_conversation_summarization_compresses_old_turns() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        for index in range(30):
            assert log_turn(
                db_path,
                session_id="s1",
                role="user" if index % 2 == 0 else "sam",
                message=f"turn {index} about memory architecture",
                action="chat",
            ).ok

        result = summarize_conversation_if_needed(db_path, session_id="s1", max_raw_turns=10)
        recent = recall_recent_conversation(db_path, limit=50)
        context = build_compact_context(db_path, session_id="s1", query="memory architecture")

        assert result.ok
        assert len(recent) == 10
        assert "Older conversation summary" in context["recent_summary"]


def test_recent_conversation_context_is_session_scoped() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        assert log_turn(db_path, session_id="s1", role="user", message="estate memory planning", action="chat").ok
        assert log_turn(db_path, session_id="s2", role="user", message="unrelated payroll note", action="chat").ok

        context = build_compact_context(db_path, session_id="s1", query="estate memory")
        recent_messages = [item["message"] for item in context["recent_conversation"]]

        assert "estate memory planning" in recent_messages
        assert "unrelated payroll note" not in recent_messages


def test_rejected_low_value_memory_is_logged() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        result, memory_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="User mentioned a temporary idea about dashboard colors.",
                memory_type="semantic",
                importance_score=3,
                confidence_score=0.8,
                source_conversation_id="c1",
                source_session_id="s1",
            ),
        )
        reviews = recent_memory_review_log(db_path, action="rejected")

        assert result.ok
        assert memory_id is None
        assert reviews[0]["rejection_reason"] == "low_importance"
        assert reviews[0]["source_session_id"] == "s1"


def test_duplicate_memory_is_logged_and_not_saved_twice() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        candidate = MemoryCandidate(
            content="The user prefers concise status updates.",
            memory_type="semantic",
            importance_score=8,
            confidence_score=0.8,
            source_conversation_id="c1",
            tags=["preference"],
        )

        first_result, first_id = save_memory_candidate(db_path, candidate)
        second_result, second_id = save_memory_candidate(db_path, candidate)
        memories = search_memories(db_path, "concise status updates", memory_types=["semantic"])
        reviews = recent_memory_review_log(db_path, action="rejected")

        assert first_result.ok and second_result.ok
        assert first_id == second_id
        assert len(memories) == 1
        assert reviews[0]["rejection_reason"] == "duplicate"


def test_improved_memory_updates_existing_memory() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        _, first_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="The user prefers concise updates.",
                memory_type="semantic",
                importance_score=7,
                confidence_score=0.65,
                source_conversation_id="c1",
                tags=["preference"],
            ),
        )
        _, second_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="The user prefers concise status updates during long coding tasks.",
                memory_type="semantic",
                importance_score=9,
                confidence_score=0.85,
                source_conversation_id="c2",
                tags=["preference", "communication"],
            ),
        )
        memories = search_memories(db_path, "concise coding status updates", memory_types=["semantic"])
        reviews = recent_memory_review_log(db_path, action="merged")

        assert first_id == second_id
        assert "long coding tasks" in memories[0]["content"]
        assert memories[0]["confidence_score"] == 0.85
        assert reviews[0]["dedupe_result"] == "merged"


def test_contradictory_memory_is_flagged_for_review() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        _, first_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="Sam should ask before coding in the Estate project.",
                memory_type="procedural",
                importance_score=8,
                confidence_score=0.8,
                source_conversation_id="c1",
                tags=["procedure"],
            ),
        )
        result, second_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="Sam should not ask before coding in the Estate project.",
                memory_type="procedural",
                importance_score=8,
                confidence_score=0.78,
                source_conversation_id="c2",
                tags=["procedure"],
            ),
        )
        conflicts = recent_memory_review_log(db_path, action="conflict")
        memories = search_memories(db_path, "ask before coding Estate", memory_types=["procedural"])

        assert result.ok
        assert second_id is None
        assert conflicts[0]["rejection_reason"] == "contradicted_existing_memory"
        assert conflicts[0]["existing_memory_id"] == first_id
        assert memories[0]["content"] == "Sam should ask before coding in the Estate project."


def test_explicit_user_instruction_beats_inferred_old_memory() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        _, first_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="Sam should ask before coding in the Estate project.",
                memory_type="procedural",
                importance_score=7,
                confidence_score=0.65,
                source_conversation_id="c1",
                tags=["procedure"],
            ),
        )
        _, second_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="Sam should not ask before coding in the Estate project.",
                memory_type="procedural",
                importance_score=9,
                confidence_score=0.95,
                source_conversation_id="c2",
                tags=["procedure", "explicit"],
                explicit=True,
            ),
        )
        memories = search_memories(db_path, "ask before coding Estate", memory_types=["procedural"])
        reviews = recent_memory_review_log(db_path, action="merged")

        assert first_id == second_id
        assert memories[0]["content"] == "Sam should not ask before coding in the Estate project."
        assert memories[0]["metadata"]["explicit"] is True
        assert "explicit_newer_instruction" in reviews[0]["review_notes"]


def test_compact_context_reports_injected_memories() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        _, memory_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                content="Sam should use concise status updates for memory work.",
                memory_type="procedural",
                importance_score=8,
                confidence_score=0.8,
                source_conversation_id="c1",
                tags=["memory", "procedure"],
            ),
        )

        context = build_compact_context(db_path, session_id="s1", query="memory status updates")
        report = memory_debug_report(db_path)

        assert memory_id in context["injected_memory_ids"]
        assert str(memory_id) in report["latest_compact_context"]["memory_ids"]
        assert report["latest_compact_context"]["memory_count"] == 1


def test_consolidation_merges_duplicate_memories() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        save_memory_candidate(
            db_path,
            MemoryCandidate("Sam should use concise status updates for memory work.", "procedural", 8, 0.8, "c1"),
        )
        save_memory_candidate(
            db_path,
            MemoryCandidate("Sam should use concise progress updates for memory work.", "procedural", 8, 0.82, "c2"),
        )

        result = consolidate_memories(db_path)
        memories = search_memories(db_path, "concise memory updates", memory_types=["procedural"], limit=10)
        logs = recent_consolidation_log(db_path)

        assert result.ok
        assert len(memories) == 1
        assert any(item["action"] == "merged" for item in logs)


def test_stale_memory_decays_and_can_archive() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        result, memory_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                "Temporary launch assumption for an old dashboard.",
                "semantic",
                6,
                0.5,
                "c1",
                created_at="2020-01-01T00:00:00+00:00",
                updated_at="2020-01-01T00:00:00+00:00",
            ),
        )
        assert result.ok and memory_id is not None

        consolidate_memories(db_path, stale_days=1)
        report = memory_debug_report(db_path)

        assert any(item["action"] in {"decayed", "archived"} for item in report["recent_consolidations"])


def test_explicit_user_instruction_survives_decay() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        _, memory_id = save_memory_candidate(
            db_path,
            MemoryCandidate(
                "Sam should always ask before deleting files.",
                "procedural",
                8,
                0.95,
                "c1",
                created_at="2020-01-01T00:00:00+00:00",
                updated_at="2020-01-01T00:00:00+00:00",
                explicit=True,
            ),
        )

        consolidate_memories(db_path, stale_days=1)
        memories = search_memories(db_path, "ask before deleting files", memory_types=["procedural"])

        assert memory_id in [item["id"] for item in memories]
        assert all(not item.get("archived") for item in memories)


def test_active_project_tracking_works() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        result, project_id = upsert_project_state(
            db_path,
            project_id="sam",
            title="Sam project",
            description="Assistant runtime",
            status="active",
            current_focus="memory consolidation",
            next_steps=["run full tests"],
            blockers=["vector search pending"],
            last_discussed_topic="long-term memory",
        )
        state = get_project_state(db_path, "sam")

        assert result.ok
        assert project_id == "sam"
        assert state is not None
        assert state["current_focus"] == "memory consolidation"
        assert state["next_steps"] == ["run full tests"]


def test_project_linked_memories_are_prioritized_and_unrelated_excluded() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        _, sam_memory = save_memory_candidate(
            db_path,
            MemoryCandidate(
                "Sam project architecture direction is to keep memory in SQLite.",
                "semantic",
                8,
                0.85,
                "c1",
                related_project_id="sam",
                tags=["architecture"],
            ),
        )
        save_memory_candidate(
            db_path,
            MemoryCandidate(
                "BulkBay project uses a marketplace checkout flow.",
                "semantic",
                8,
                0.85,
                "c2",
                related_project_id="bulkbay",
                tags=["architecture"],
            ),
        )
        upsert_project_state(
            db_path,
            project_id="sam",
            title="Sam project",
            current_focus="memory architecture",
            related_memories=[sam_memory or 0],
        )

        context = build_compact_context(db_path, session_id="s1", query="architecture direction", active_project_id="sam")
        contents = [
            item["content"]
            for items in context["relevant_memories"].values()
            for item in items
        ]

        assert any("Sam project" in content for content in contents)
        assert not any("BulkBay" in content for content in contents)


def test_session_recap_generation_works() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        log_turn(db_path, session_id="s1", role="user", message="We decided Sam memory stays in SQLite.", action="chat")
        log_turn(db_path, session_id="s1", role="sam", message="Next action is to run full tests.", action="chat")
        log_turn(db_path, session_id="s1", role="user", message="Unresolved question: vector search timing.", action="chat")

        result, recap_id = generate_session_recap(db_path, session_id="s1", project_id="sam")

        assert result.ok
        assert recap_id is not None
        assert result.metadata["recap_id"] == recap_id


def test_archived_memories_are_not_prioritized() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        save_memory_candidate(
            db_path,
            MemoryCandidate(
                "Old noisy dashboard assumption for Sam memory.",
                "semantic",
                6,
                0.4,
                "c1",
                created_at="2020-01-01T00:00:00+00:00",
                updated_at="2020-01-01T00:00:00+00:00",
            ),
        )
        consolidate_memories(db_path, stale_days=1)

        context = build_compact_context(db_path, session_id="s1", query="dashboard assumption memory")
        contents = [
            item["content"]
            for items in context["relevant_memories"].values()
            for item in items
        ]

        assert not any("Old noisy dashboard" in content for content in contents)
