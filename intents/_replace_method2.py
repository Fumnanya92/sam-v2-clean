#!/usr/bin/env python3
"""Replace old _register_executor_tools with thin wrapper."""

with open('intents/router.py', 'r') as f:
    lines = f.readlines()

# Find the start and end of the method
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if 'def _register_executor_tools(self)' in line:
        start_idx = i - 2  # Include the comment lines above
        break

# Find the next method definition
for i in range(start_idx + 1, len(lines)):
    if 'def _execute_with_planner' in lines[i]:
        end_idx = i
        break

if start_idx is not None and end_idx is not None:
    # Create the new method
    new_method = '''    # =========================================================================
    # Phase 1: Executive Tool Registration
    # =========================================================================

    def _register_executor_tools(self) -> None:
        """Register all tools/intents as executable handlers.

        This delegates to the comprehensive tool registry in _executor_tools_registry.
        All intent business logic is now extracted into reusable tool handlers.
        """
        register_all_executor_tools(self)

    '''
    
    # Replace the old lines with the new method
    new_lines = lines[:start_idx] + [new_method] + lines[end_idx:]
    
    with open('intents/router.py', 'w') as f:
        f.writelines(new_lines)
    
    print(f'✓ Replaced _register_executor_tools() (removed lines {start_idx}-{end_idx})')
else:
    print(f'✗ Could not find method boundaries: start={start_idx}, end={end_idx}')
