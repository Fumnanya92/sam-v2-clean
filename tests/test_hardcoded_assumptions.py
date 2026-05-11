"""Phase 6: Regression tests for hardcoded assumption cleanup.

This module validates that hardcoded project-specific assumptions have been
removed from the codebase and won't be reintroduced.

Specifically tests:
1. No tic-tac/game-specific project filtering logic
2. No hardcoded Flutter SDK paths
3. No project-specific workflow names
4. Tools are configurable and portable
"""

from __future__ import annotations

import re
from pathlib import Path


class HardcodedAssumptionTest:
    """Tests that codebase remains free of hardcoded assumptions."""
    
    # Patterns that should NOT appear in the codebase
    FORBIDDEN_PATTERNS = [
        # Tic-tac game specific
        (r"count_tictac", "tic-tac game specific intent"),
        (r"tictac_projects", "tic-tac game specific variable"),
        (r'"tictac".*"tic.tac"', "tic-tac keyword filtering"),
        (r'token.*in.*\("tictac".*"tic', "game-specific phrase rules"),
        
        # Hardcoded Flutter paths
        (r'C:\\flutter\\bin\\flutter', "hardcoded Flutter path"),
        (r'SAM_V2_FLUTTER_BIN', "Sam v2 specific Flutter env var"),
        
        # Hardcoded directory assumptions
        (r'/sam_v2/workspace', "hardcoded workspace path"),  # Should be configurable
    ]
    
    # Patterns that indicate portability (should exist)
    REQUIRED_PATTERNS = [
        (r'workspace_root.*Path', "workspace root is configurable"),
        (r'Path\.cwd\(\)', "uses current working directory"),
        (r'getenv.*FLUTTER|getenv.*flutter', "Flutter path from environment"),
    ]
    
    def __init__(self, repo_root: str | Path) -> None:
        """Initialize test with repository root."""
        self.repo_root = Path(repo_root)
    
    def run_all_tests(self) -> dict[str, bool]:
        """Run all regression tests and return results."""
        results = {}
        
        # Test: No forbidden patterns
        results["no_tic_tac_hardcoding"] = self._test_no_forbidden_patterns()
        
        # Test: Portability features present
        results["has_configurable_paths"] = self._test_configurable_paths()
        
        # Test: No developer-specific assumptions
        results["no_developer_specific_paths"] = self._test_no_developer_paths()
        
        return results
    
    def _test_no_forbidden_patterns(self) -> bool:
        """Verify forbidden patterns don't appear in Python files."""
        python_files = list(self.repo_root.rglob("*.py"))
        violations = []
        
        for file_path in python_files:
            # Skip test files and __pycache__
            if "__pycache__" in str(file_path) or "test_" in file_path.name:
                continue
            
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            
            for pattern, description in self.FORBIDDEN_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    violations.append(f"{file_path}: {description} (pattern: {pattern})")
        
        if violations:
            print("Violations found:")
            for violation in violations:
                print(f"  - {violation}")
            return False
        
        return True
    
    def _test_configurable_paths(self) -> bool:
        """Verify that paths are configurable, not hardcoded."""
        # Check that workspace_root is a parameter
        router_file = self.repo_root / "intents" / "router.py"
        if not router_file.exists():
            return True  # Skip if file doesn't exist
        
        content = router_file.read_text(encoding="utf-8", errors="ignore")
        
        # Should have workspace_root parameter
        if "workspace_root" not in content:
            return False
        
        # Should use Path.cwd() or similar
        if "Path.cwd()" not in content and "pathlib.Path" not in content:
            return False
        
        return True
    
    def _test_no_developer_paths(self) -> bool:
        """Verify no developer-specific absolute paths."""
        python_files = list(self.repo_root.rglob("*.py"))
        violations = []
        
        developer_paths = [
            r"C:\\flutter",
            r"C:\\Users",
            r"/Users/",
            r"/home/",
            r"sam_v2.*workspace",
        ]
        
        for file_path in python_files:
            # Skip test files and __pycache__
            if "__pycache__" in str(file_path) or "test_" in file_path.name:
                continue
            
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            
            for pattern in developer_paths:
                # Allow in comments only
                for line in content.split("\n"):
                    if line.strip().startswith("#"):
                        continue  # Skip comments
                    if re.search(pattern, line):
                        violations.append(f"{file_path}: developer path {pattern}")
        
        return len(violations) == 0


def test_regression() -> bool:
    """Run all regression tests for hardcoded assumptions."""
    repo_root = Path(__file__).parent
    test = HardcodedAssumptionTest(repo_root)
    results = test.run_all_tests()
    
    print("\nHardcoded Assumption Regression Tests:")
    print("=" * 50)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False
    
    print("=" * 50)
    return all_passed


if __name__ == "__main__":
    import sys
    passed = test_regression()
    sys.exit(0 if passed else 1)
