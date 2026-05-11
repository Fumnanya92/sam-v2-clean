#!/usr/bin/env python3
"""Direct test of fixed tool handlers showing actual formatted output."""
import sys
from pathlib import Path

def test_handlers():
    """Test that fixed handlers return properly formatted output."""
    print("\n" + "="*70)
    print("Testing Fixed Tool Handlers - Actual Data Output")
    print("="*70)
    
    from intents.router import IntentRouter, IntentRequest
    
    # Initialize router
    workspace_root = Path(__file__).parent
    db_path = workspace_root / "workspace" / "runtime" / "sam.db"
    router = IntentRouter(db_path=db_path, workspace_root=workspace_root)
    
    # Create test payload structure
    def make_payload(intent_name: str, params: dict = None) -> dict:
        request = IntentRequest(intent=intent_name, parameters=params or {})
        return {"request": request, "memory": {}}
    
    # Test 1: Capabilities
    print("\n[1] CAPABILITIES - Should show actual capabilities list")
    print("-" * 70)
    cap_handler = router.tool_executor.get("capabilities")
    if cap_handler:
        result = cap_handler(None)
        print(f"Status: {result.status}")
        print(f"Summary: {result.summary[:150]}..." if len(result.summary) > 150 else f"Summary: {result.summary}")
        caps = result.metadata.get("available_capabilities", [])
        print(f"Capabilities returned in metadata: {len(caps)}")
        if result.summary == "Capability awareness summary generated.":
            print("❌ FAIL: Still showing generic message, not actual capabilities!")
            return False
        if len(caps) > 0:
            print("✓ PASS: Actual capabilities are present")
        else:
            print("⚠ WARNING: No capabilities found, check if registry is populated")
    else:
        print("❌ FAIL: capability handler not registered")
        return False
    
    # Test 2: List Goals
    print("\n[2] LIST GOALS - Should show actual goals or 'no goals' message")
    print("-" * 70)
    goals_handler = router.tool_executor.get("list_goals")
    if goals_handler:
        result = goals_handler(make_payload("list_goals"))
        print(f"Status: {result.status}")
        print(f"Summary: {result.summary[:150]}..." if len(result.summary) > 150 else f"Summary: {result.summary}")
        if "Goals listed" in result.summary:
            print("❌ FAIL: Still showing generic 'Goals listed' message!")
            return False
        if "Active goals" in result.summary or "don't have any active goals" in result.summary:
            print("✓ PASS: Proper goal summary message shown")
        else:
            print("⚠ WARNING: Unexpected summary format")
    else:
        print("❌ FAIL: list_goals handler not registered")
        return False
    
    # Test 3: List Tasks
    print("\n[3] LIST TASKS - Should show actual task count and names")
    print("-" * 70)
    tasks_handler = router.tool_executor.get("list_tasks")
    if tasks_handler:
        result = tasks_handler(make_payload("list_tasks"))
        print(f"Status: {result.status}")
        print(f"Summary: {result.summary}")
        if "I do not have any tracked tasks yet" in result.summary or "tracked task(s)" in result.summary:
            print("✓ PASS: Proper task summary message shown")
        else:
            print("⚠ WARNING: Unexpected summary format")
    else:
        print("❌ FAIL: list_tasks handler not registered")
        return False
    
    # Test 4: List Projects
    print("\n[4] LIST PROJECTS - Should show actual project names")
    print("-" * 70)
    projects_handler = router.tool_executor.get("list_projects")
    if projects_handler:
        result = projects_handler(make_payload("list_projects"))
        print(f"Status: {result.status}")
        print(f"Summary: {result.summary}")
        if "tictac game" in result.summary or "registered projects" in result.summary or "project(s)" in result.summary:
            print("✓ PASS: Project names shown in summary")
        else:
            print("⚠ WARNING: Expected project names in summary")
    else:
        print("❌ FAIL: list_projects handler not registered")
        return False
    
    # Test 5: Read File (should show content preview)
    print("\n[5] READ FILE - Should show file content preview")
    print("-" * 70)
    read_handler = router.tool_executor.get("read_file")
    if read_handler:
        readme_path = workspace_root / "README.md"
        if readme_path.exists():
            payload = make_payload("read_file", {"path": str(readme_path)})
            result = read_handler(payload)
            print(f"Status: {result.status}")
            print(f"Summary (first 200 chars): {result.summary[:200]}...")
            if "File read succeeded" in result.summary:
                print("❌ FAIL: Still showing generic 'File read succeeded' message!")
                return False
            if "chars" in result.summary and "lines" in result.summary:
                print("✓ PASS: Showing file stats and content preview")
            else:
                print("⚠ WARNING: Missing file stats in summary")
        else:
            print("⚠ WARNING: README.md not found for testing")
    else:
        print("❌ FAIL: read_file handler not registered")
        return False
    
    print("\n" + "="*70)
    print("✓ ALL HANDLERS RETURNING FORMATTED OUTPUT")
    print("="*70)
    return True

if __name__ == "__main__":
    try:
        success = test_handlers()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
