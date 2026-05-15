"""Debug model client availability."""
from llm import OllamaClient

model_client = OllamaClient()
print(f"Model client: {model_client}")
print(f"Model client is None: {model_client is None}")
print(f"Has _request: {hasattr(model_client, '_request')}")
print(f"_request: {getattr(model_client, '_request', None)}")

# Try calling it
try:
    result = model_client._request("POST", "/api/generate", {"model": "test"})
    print(f"Request result: {result}")
except Exception as e:
    print(f"Exception: {type(e).__name__}: {e}")

print("\nNow testing action_gate...")
from core.action_gate import ActionGate

gate = ActionGate(model_client=model_client)
print(f"Gate model_client: {gate.model_client}")

# Test a conversational message
conv = gate.decide("I will test you when I am done.", memory_block=None)
print(f"\nConversational: {conv.to_dict()}")

# Test an action message
action = gate.decide("Go run the tests now.", memory_block=None)
print(f"Action: {action.to_dict()}")
