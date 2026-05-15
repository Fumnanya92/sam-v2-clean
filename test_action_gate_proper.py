"""Test action gate with proper Ollama integration."""

from core.action_gate_v2 import ActionGate
from llm import OllamaClient

# Initialize with real Ollama client
model_client = OllamaClient()
print(f"Model resolved: {model_client.resolve_model()}")

gate = ActionGate(model_client=model_client)

# Test examples from specification
test_cases = [
    # Conversational (should_act=false)
    ("I will test you when I am done.", False),
    ("We need to test this later.", False),
    ("I am still fixing you.", False),
    ("The button may need fixing.", False),
    ("I want browser automation later.", False),
    ("When I say go fix it, then you should use Codex.", False),
    ("We should add memory before coding.", False),
    ("I'm talking about the code, not asking you to code.", False),
    ("Sam should know when to code.", False),
    ("I don't want you to call Codex in the middle of conversation.", False),
    
    # Action requests (should_act=true)
    ("Go run the tests now.", True),
    ("Use Codex to fix the login button.", True),
    ("Open the repo and inspect app.py.", True),
    ("Create a new file called memory_debug.py.", True),
    ("Deploy this now.", True),
    ("Push the changes to git.", True),
    ("Check the repo and tell me why the button is broken.", True),
]

print("\n" + "=" * 80)
print("ACTION GATE TEST RESULTS - SPECIFICATION EXAMPLES")
print("=" * 80)

passed = 0
failed = 0

for text, expected_action in test_cases:
    decision = gate.decide(text, memory_block=None)
    is_correct = bool(decision.should_act) == expected_action
    status = "[PASS]" if is_correct else "[FAIL]"
    
    if is_correct:
        passed += 1
    else:
        failed += 1
    
    action_str = "ACTION" if decision.should_act else "CONVERSATIONAL"
    print(f"\n{status} | {action_str}")
    print(f"  Text: {text[:60]}...")
    print(f"  Intent: {decision.action_type}")
    print(f"  Reason: {decision.reason[:70]}...")
    print(f"  Confidence: {decision.confidence:.2f}")

print("\n" + "=" * 80)
print(f"RESULTS: {passed}/{len(test_cases)} PASSED ({100*passed//len(test_cases)}%)")
print(f"         {failed}/{len(test_cases)} FAILED")
print("=" * 80)

# Sample outputs for documentation
print("\n\n" + "=" * 80)
print("SAMPLE ACTION GATE OUTPUTS FOR DOCUMENTATION")
print("=" * 80)

print("\n--- CONVERSATIONAL MESSAGE EXAMPLE ---")
conv_decision = gate.decide("I will test you when I am done.", memory_block=None)
print("Input: 'I will test you when I am done.'")
print("Output JSON:")
print("{")
print(f'  "should_act": {str(conv_decision.should_act).lower()},')
print(f'  "action_type": "{conv_decision.action_type}",')
print(f'  "reason": "{conv_decision.reason}",')
print(f'  "confidence": {conv_decision.confidence:.2f}')
print("}")

print("\n--- ACTION REQUEST EXAMPLE ---")
action_decision = gate.decide("Go run the tests now.", memory_block=None)
print("Input: 'Go run the tests now.'")
print("Output JSON:")
print("{")
print(f'  "should_act": {str(action_decision.should_act).lower()},')
print(f'  "action_type": "{action_decision.action_type}",')
print(f'  "reason": "{action_decision.reason}",')
print(f'  "confidence": {action_decision.confidence:.2f}')
print("}")
