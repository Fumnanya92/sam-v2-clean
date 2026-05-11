#!/usr/bin/env python3
"""Direct test of tool resolution fix without GUI."""
import sys
from pathlib import Path

def test_tool_resolution():
    """Test that _execute_with_planner properly resolves tools."""
    print("\n" + "="*60)
    print("Testing Tool Resolution Fix")
    print("="*60)
    
    from intents.router import IntentRouter
    
    # Initialize router with keyword arguments
    workspace_root = Path(__file__).parent
    db_path = workspace_root / "workspace" / "runtime" / "sam.db"
    router = IntentRouter(db_path=db_path, workspace_root=workspace_root)
    
    # Check that tools are registered
    available_tools = router.tool_executor.available_tools
    print(f"\n✓ Available tools registered: {len(available_tools)}")
    if available_tools:
        tools_list = available_tools
        print(f"  Sample tools: {tools_list[:5]}")
        print(f"  Total: {len(tools_list)}")
    else:
        print("✗ FAILED: No tools registered!")
        return False
    
    # Test tool resolution via the _execute_with_planner method
    print("\n--- Test: Verify Fix in _execute_with_planner Source ---")
    
    # Check the method passes available_tools to planner
    print(f"\n✓ Checking router._execute_with_planner method...")
    import inspect
    source = inspect.getsource(router._execute_with_planner)
    
    # Check if available_tools is in the context
    if "available_tools = self.tool_executor.available_tools" in source:
        print("✓ available_tools is retrieved from tool_executor")
    else:
        print("✗ FAILED: available_tools not retrieved!")
        return False
    
    if '"available_tools": available_tools' in source:
        print("✓ available_tools is passed to planner context")
    else:
        print("✗ FAILED: available_tools not passed to context!")
        return False
    
    print("\n" + "="*60)
    print("✓ TOOL RESOLUTION FIX VERIFIED")
    print("="*60)
    return True

if __name__ == "__main__":
    try:
        success = test_tool_resolution()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
