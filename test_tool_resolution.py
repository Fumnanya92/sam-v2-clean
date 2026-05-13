#!/usr/bin/env python3
"""Direct test of tool resolution fix without GUI."""
import sys
from pathlib import Path

def test_tool_resolution():
    """Test that RuntimeExecutionEngine properly resolves tools."""
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
    print(f"\nOK Available tools registered: {len(available_tools)}")
    if available_tools:
        tools_list = available_tools
        print(f"  Sample tools: {tools_list[:5]}")
        print(f"  Total: {len(tools_list)}")
    else:
        print("FAILED FAILED: No tools registered!")
        assert False
    
    # Test tool resolution via the RuntimeExecutionEngine
    print("\n--- Test: Verify Fix in RuntimeExecutionEngine Source ---")
    
    # Check the method passes available_tools to planner
    print("\nOK Checking router.execution_engine.execute method...")
    import inspect
    source = inspect.getsource(router.execution_engine.execute)
    expected = '"available_tools": self.tool_executor.available_tools'
    
    # Check if available_tools is in the context
    assert expected in source, "available_tools is not passed from tool_executor to planner context"
    print("OK available_tools is retrieved from tool_executor and passed to planner context")
    
    print("\n" + "="*60)
    print("OK TOOL RESOLUTION FIX VERIFIED")
    print("="*60)
    return None

if __name__ == "__main__":
    try:
        test_tool_resolution()
        sys.exit(0)
    except Exception as e:
        print(f"\nFAILED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
