#!/usr/bin/env python3
"""Fix tool resolution bug by passing available_tools to planner."""
import re

with open('intents/router.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the _execute_with_planner method
start_idx = None
for i, line in enumerate(lines):
    if 'def _execute_with_planner' in line:
        start_idx = i
        break

if start_idx is None:
    print("✗ Could not find _execute_with_planner method")
    exit(1)

# Find the end of the method (next def at same indentation level)
end_idx = len(lines)
for i in range(start_idx + 1, len(lines)):
    if lines[i].startswith('    def ') and not lines[i].startswith('        '):
        end_idx = i
        break

print(f"Found method from line {start_idx+1} to {end_idx}")

# New method code
new_method_lines = '''    def _execute_with_planner(self, request: IntentRequest, memory_block: dict[str, Any] | None) -> SamResult:
        """Create a plan and execute with observation loop for adaptive execution.

        Phase 5 plan-act-observe-continue cycle:
        1. Plan: TaskPlanner generates direct or multi-step plan
        2. Act: ObservationLoop executes via WorkerCentricExecutor with monitoring
        3. Observe: Extracts observations and results
        4. Continue: Makes adaptive decisions (retry, skip, ask user, etc.)
        """
        # Get available tools for planning context
        available_tools = self.tool_executor.available_tools
        
        # Create planning context with intent, available tools, and memory
        plan = self.task_planner.plan(
            request.intent,
            context={
                "request": request,
                "memory": memory_block,
                "intent": request.intent,
                "available_tools": available_tools,
            }
        )
        
        # Use observation loop for adaptive execution (handles direct and multi-step modes)
        result, step_executions = self.observation_loop.execute_plan(plan, memory_block)
        
        # Attach execution metadata
        result.metadata.setdefault("execution_steps", len(step_executions))
        result.metadata.setdefault("plan_mode", plan.mode)
        result.metadata.setdefault("request_intent", request.intent)
        
        return result

'''.split('\n')

# Replace the method
new_lines = lines[:start_idx] + [line + '\n' for line in new_method_lines] + lines[end_idx:]

with open('intents/router.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✓ Method updated successfully")
