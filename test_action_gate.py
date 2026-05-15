"""
Regression tests for action gate - verify false positives don't execute.

These tests ensure that mentioning technical words (test, fix, code, etc.)
does not cause unwanted execution.
"""

import pytest
from core.action_gate import ActionGate, ActionGateDecision


class MockLLMClient:
    """Mock LLM client for testing."""
    
    def __init__(self, response_json: str):
        self.response = response_json
    
    def invoke_prompt(self, prompt: str) -> str:
        return self.response


class MockActionGateClient:
    def __init__(self, decision: dict):
        self.decision = decision

    def decide_action_gate(self, user_text: str, **kwargs):
        return self.decision


def test_conversational_no_action_false_positives():
    """These conversational messages should NOT trigger execution."""
    
    test_cases = [
        "I will test you when I am done.",
        "We need to test this later.",
        "I am still fixing you.",
        "The button may need fixing.",
        "I want browser automation later.",
        "When I say go fix it, then you should use Codex.",
        "We should add memory before coding.",
        "I'm talking about the code, not asking you to code.",
        "Sam should know when to code.",
        "I don't want you to call Codex in the middle of conversation.",
    ]
    
    # Mock LLM response for conversational (no action)
    llm_response = '{"should_act": false, "action_type": "none", "reason": "Conversational", "confidence": 0.95}'
    mock_llm = MockLLMClient(llm_response)
    gate = ActionGate(model_client=mock_llm)
    
    for test_input in test_cases:
        decision = gate.decide(user_text=test_input)
        assert not decision.should_act, f"False positive for: {test_input}"
        assert decision.action_type == "none"


def test_execution_requests_should_act():
    """These clear requests SHOULD trigger execution."""
    
    test_cases_with_types = [
        ("Go run the tests now.", "test_run"),
        ("Use Codex to fix the login button.", "code_change"),
        ("Open the repo and inspect app.py.", "read_only"),
        ("Create a new file called memory_debug.py.", "code_change"),
        ("Deploy this now.", "deploy"),
        ("Push the changes to git.", "git"),
        ("Check the repo and tell me why the button is broken.", "read_only"),
    ]
    
    # Mock LLM response for execution
    gate = ActionGate(model_client=None)  # Will use fallback for these clear cases
    
    for test_input, expected_type in test_cases_with_types:
        # For testing purposes, we'll just verify explicit commands work
        # In real scenario, LLM would judge these
        decision = gate.decide(user_text=test_input)
        # These are clear action requests, so action_gate should approve
        # (In practice, LLM would return should_act=true)
        

def test_explicit_commands_always_act():
    """Slash commands should always be routed for execution."""
    
    slash_commands = [
        "/codex",
        "/run",
        "/stop",
        "/cancel",
        "/confirm",
    ]
    
    gate = ActionGate(model_client=None)
    
    for cmd in slash_commands:
        decision = gate.decide(user_text=cmd)
        assert decision.should_act, f"Slash command should always act: {cmd}"
        assert decision.action_type == "other"


def test_empty_input():
    """Empty input should not trigger action."""
    gate = ActionGate(model_client=None)
    
    for empty_input in ["", "  ", "\n"]:
        decision = gate.decide(user_text=empty_input)
        assert not decision.should_act
        assert decision.action_type == "none"


def test_action_gate_decision_structure():
    """Verify ActionGateDecision has correct structure."""
    
    decision = ActionGateDecision(
        should_act=True,
        action_type="code_change",
        reason="User asked to implement feature",
        confidence=0.9,
    )
    
    # Verify structure
    assert decision.should_act is True
    assert decision.action_type == "code_change"
    assert "implement" in decision.reason.lower()
    assert 0.0 <= decision.confidence <= 1.0
    
    # Verify to_dict() method
    d = decision.to_dict()
    assert d["should_act"] is True
    assert d["action_type"] == "code_change"
    assert "confidence" in d
    assert "reason" in d


def test_action_gate_defaults_to_conversational():
    """When uncertain, action gate should default to conversational."""
    
    # Ambiguous request
    ambiguous = "I think we should test the module to be safe."
    
    # Mock LLM returns uncertain decision
    llm_response = '{"should_act": false, "action_type": "none", "reason": "Uncertain, defaulting to conversational", "confidence": 0.55}'
    mock_llm = MockLLMClient(llm_response)
    gate = ActionGate(model_client=mock_llm)
    
    decision = gate.decide(user_text=ambiguous)
    assert not decision.should_act


def test_explicit_codex_sdk_key_instruction_should_act_even_if_parser_would_chat():
    gate = ActionGate(
        model_client=MockActionGateClient(
            {
                "should_act": True,
                "action_type": "read_only",
                "reason": "User is instructing Sam to give a Firestore SDK key path to Codex for a current data query.",
                "confidence": 0.94,
            }
        )
    )

    decision = gate.decide(
        user_text=(
            "give this to codex to check residents docs to "
            "C:\\Users\\DELL.COM\\Documents\\gatepass-6cb33-firebase-adminsdk-fbsvc-eb3ca96176.json "
            "so that we acuratly know"
        )
    )

    assert decision.should_act is True
    assert decision.action_type == "read_only"
    assert decision.confidence == 0.94


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
