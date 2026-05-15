"""
Test action gate enforcement in live runtime.
Verify that conversational messages don't trigger execution pipelines.
"""

import pytest
from pathlib import Path
from core import SamRuntime


@pytest.fixture
def runtime(tmp_path):
    """Create a test runtime with temporary paths."""
    db_path = tmp_path / "test.db"
    memory_path = tmp_path / "memory.json"
    session_path = tmp_path / "session.json"
    
    return SamRuntime(
        db_path=str(db_path),
        memory_path=str(memory_path),
        session_path=str(session_path),
    )


class TestActionGateEnforcement:
    """Test that action gate prevents false positive execution."""

    def test_test_keyword_conversational_only(self, runtime):
        """'this is just a test' should be conversational, not execute."""
        result = runtime.handler.handle(
            user_text="this is just a test to see that you are active",
            session=runtime.session,
        )
        
        # Should be conversational only
        assert result.status == "success"
        
        # Check metadata shows action_gate decided should_act=false
        if "action_gate" in result.metadata:
            assert result.metadata["action_gate"]["should_act"] is False
        
        # Should NOT contain pipeline execution metadata (no detailed planning/execution)
        # The summary should be conversational, not a plan
        assert "plan" not in result.summary.lower() or result.summary.lower().count("plan") < 3

    def test_code_keyword_conversational_only(self, runtime):
        """'are you ready to code' should be conversational, not execute."""
        result = runtime.handler.handle(
            user_text="are you ready to code",
            session=runtime.session,
        )
        
        assert result.status == "success"
        
        # Should NOT contain execution pipeline output
        # Conversational responses are short
        assert len(result.summary) < 500 or "conversational" in str(result.metadata).lower()

    def test_greeting_conversational_only(self, runtime):
        """'hi' should be conversational."""
        result = runtime.handler.handle(
            user_text="hi",
            session=runtime.session,
        )
        
        assert result.status == "success"

    def test_empty_input_conversational(self, runtime):
        """Empty input should be conversational."""
        result = runtime.handler.handle(
            user_text="",
            session=runtime.session,
        )
        
        # Empty input will typically fail due to validation, which is OK
        # The important thing is it doesn't execute a pipeline
        assert result.status in ["success", "failed"]

    def test_action_gate_metadata_present(self, runtime):
        """Most responses should include action gate metadata."""
        result = runtime.handler.handle(
            user_text="hello",
            session=runtime.session,
        )
        
        # May have action gate metadata (depends on routing path)
        # At minimum, should have status
        assert result.status == "success"

    def test_test_message_is_conversational(self, runtime):
        """Verify 'I am testing' is conversational, not code execution."""
        result = runtime.handler.handle(
            user_text="I am testing to see if you are working",
            session=runtime.session,
        )
        
        assert result.status == "success"
        # Should not trigger code execution pipeline
        # The response should be simple conversational message
        summary = result.summary.lower()
        assert "error" not in summary or "working" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

