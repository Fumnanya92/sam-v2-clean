#!/usr/bin/env python3
"""Script to replace _register_executor_tools() with thin wrapper."""

import re

with open('intents/router.py', 'r') as f:
    content = f.read()

# Find and replace the _register_executor_tools method
pattern = r'    # .*?Phase 1 planner / executor helpers.*?\n    def _register_executor_tools\(self\).*?(?=\n    def _execute_with_planner)'
replacement = '''    # =========================================================================
    # Phase 1: Executive Tool Registration
    # =========================================================================

    def _register_executor_tools(self) -> None:
        """Register all tools/intents as executable handlers.
        
        This delegates to the comprehensive tool registry in _executor_tools_registry.
        """
        register_all_executor_tools(self)

    '''

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

with open('intents/router.py', 'w') as f:
    f.write(content)

print('✓ _register_executor_tools() method replaced')
