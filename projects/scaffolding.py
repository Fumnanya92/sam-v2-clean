"""Project scaffolding helpers for organized Sam v2 project starts."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.workers import FileWriteSpec, ToolingWorker

from .registry import ProjectRecord, ProjectRegistry

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="app-shell">
    <header class="hero">
      <p class="eyebrow">Sam v2 scaffold</p>
      <h1>{title}</h1>
      <p class="lede">A modular browser game scaffold with separate markup, styles, and logic.</p>
    </header>
    <section class="game-card">
      <p id="status">Player X's turn</p>
      <div class="board" id="board" aria-label="Tic tac toe board"></div>
      <button id="reset" type="button">Reset game</button>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""

STYLES_CSS = """:root {
  color-scheme: light;
  --bg: #f4efe6;
  --panel: #fffaf2;
  --text: #1f2933;
  --muted: #6b7280;
  --accent: #c26d3d;
  --accent-dark: #8f4e2a;
  --line: #ead8c0;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  font-family: Georgia, "Times New Roman", serif;
  color: var(--text);
  background:
    radial-gradient(circle at top, rgba(194, 109, 61, 0.18), transparent 30%),
    linear-gradient(180deg, #f7f2ea 0%, var(--bg) 100%);
}

.app-shell {
  max-width: 720px;
  margin: 0 auto;
  padding: 48px 20px 72px;
}

.hero {
  margin-bottom: 28px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 0.72rem;
  color: var(--accent-dark);
}

.lede {
  max-width: 48ch;
  color: var(--muted);
}

.game-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 24px;
  box-shadow: 0 20px 40px rgba(31, 41, 51, 0.08);
}

#status {
  font-weight: 700;
  margin-bottom: 16px;
}

.board {
  display: grid;
  grid-template-columns: repeat(3, minmax(80px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.cell {
  aspect-ratio: 1;
  border: 0;
  border-radius: 18px;
  background: white;
  color: var(--accent-dark);
  font-size: 2rem;
  font-weight: 700;
  box-shadow: inset 0 0 0 1px var(--line);
  cursor: pointer;
}

#reset {
  border: 0;
  border-radius: 999px;
  padding: 12px 18px;
  background: var(--accent);
  color: white;
  font-weight: 700;
  cursor: pointer;
}
"""

APP_JS = """const board = document.getElementById("board");
const statusText = document.getElementById("status");
const resetButton = document.getElementById("reset");

const winningLines = [
  [0, 1, 2],
  [3, 4, 5],
  [6, 7, 8],
  [0, 3, 6],
  [1, 4, 7],
  [2, 5, 8],
  [0, 4, 8],
  [2, 4, 6],
];

let cells = Array(9).fill("");
let player = "X";
let winner = "";

function render() {
  board.innerHTML = "";
  cells.forEach((value, index) => {
    const button = document.createElement("button");
    button.className = "cell";
    button.type = "button";
    button.textContent = value;
    button.addEventListener("click", () => playTurn(index));
    board.appendChild(button);
  });
  statusText.textContent = winner || `Player ${player}'s turn`;
}

function playTurn(index) {
  if (cells[index] || winner) {
    return;
  }

  cells[index] = player;
  const line = winningLines.find(([a, b, c]) => cells[a] && cells[a] === cells[b] && cells[b] === cells[c]);

  if (line) {
    winner = `Player ${player} wins!`;
  } else if (cells.every(Boolean)) {
    winner = "It's a draw!";
  } else {
    player = player === "X" ? "O" : "X";
  }

  render();
}

function resetGame() {
  cells = Array(9).fill("");
  player = "X";
  winner = "";
  render();
}

resetButton.addEventListener("click", resetGame);
render();
"""

RUN_PROJECT = """import argparse
import os
import sys
import webbrowser
from pathlib import Path


def validate(root: Path) -> Path:
    index_path = root / "index.html"
    styles_path = root / "styles.css"
    app_path = root / "app.js"

    index_text = index_path.read_text(encoding="utf-8")
    styles_text = styles_path.read_text(encoding="utf-8")
    app_text = app_path.read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="styles.css" />' in index_text
    assert '<script src="app.js"></script>' in index_text
    assert "function playTurn(index)" in app_text
    assert ".board {" in styles_text
    return index_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate the project without launching it.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    index_path = validate(root)
    launch_url = index_path.as_uri()

    if args.check:
        print("scaffold project ready")
        return 0

    if os.getenv("SAM_V2_NO_BROWSER") == "1":
        print(f"launch target {launch_url} (browser disabled by SAM_V2_NO_BROWSER)")
        return 0

    launch_error = None
    try:
        if webbrowser.open(launch_url):
            print(f"launched project at {launch_url}")
            return 0
        launch_error = "webbrowser.open returned False"
    except Exception as exc:
        launch_error = str(exc)

    if hasattr(os, "startfile"):
        try:
            os.startfile(str(index_path))
            print(f"launched project at {launch_url} via os.startfile")
            return 0
        except Exception as exc:
            launch_error = str(exc)

    print(f"failed to launch project at {launch_url}: {launch_error or 'no browser handler accepted the request'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""

README_MD = """# {title}

Created by Sam v2 as a modular HTML game scaffold.

## Structure

- `index.html`: page shell
- `styles.css`: presentation
- `app.js`: game logic
- `run_project.py`: local validation command
- `PLAN.md`: next build steps

## Run

```bash
python run_project.py
python run_project.py --check
```

If `SAM_V2_NO_BROWSER=1` is set, Sam will validate and print the launch target without opening the browser.
"""

PLAN_MD = """# Project Plan

## Goal

Build a tidy starter for a browser tic-tac-toe game.

## Modules

1. `index.html`
2. `styles.css`
3. `app.js`
4. `run_project.py`
5. `README.md`

## Next steps

1. Add score tracking.
2. Add restart animation.
3. Add a lightweight test harness for game rules.
"""


@dataclass
class ProjectScaffoldRequest:
    name: str
    project_type: str = "html_game"


class ProjectScaffolder:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        project_registry: ProjectRegistry,
        tooling_worker: ToolingWorker,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.project_registry = project_registry
        self.tooling_worker = tooling_worker

    def scaffold(self, request: ProjectScaffoldRequest) -> SamResult:
        title = request.name.strip()
        if not title:
            return SamResult(
                status="failed",
                summary="Project name is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project name",
                next_action="ask_user",
            )

        if request.project_type != "html_game":
            return SamResult(
                status="failed",
                summary="That scaffold type is not available yet.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=request.project_type,
                next_action="ask_user",
            )

        project_id = self._slugify(title)
        project_root = self.workspace_root / project_id
        project_root.mkdir(parents=True, exist_ok=True)

        file_specs = [
            ("index.html", INDEX_HTML.format(title=title), "Create the page shell for the scaffolded game."),
            ("styles.css", STYLES_CSS, "Create the stylesheet for the scaffolded game."),
            ("app.js", APP_JS, "Create the browser game logic for the scaffolded game."),
            ("README.md", README_MD.format(title=title), "Document the scaffolded project layout and run command."),
            ("PLAN.md", PLAN_MD, "Write the modular project plan for the scaffolded game."),
            ("run_project.py", RUN_PROJECT, "Create the local validation command for the scaffolded game."),
        ]

        delegation: list[dict[str, str]] = []
        for filename, content, description in file_specs:
            write_result, task = self.tooling_worker.execute_write(
                FileWriteSpec(
                    name=f"scaffold_{project_id}_{filename.replace('.', '_')}",
                    worker_type="code",
                    worker_name="Mason",
                    target_path=project_root / filename,
                    content=content,
                    description=description,
                    overwrite=True,
                )
            )
            if not write_result.ok:
                write_result.metadata.setdefault("project_id", project_id)
                write_result.metadata.setdefault("name", title)
                write_result.metadata.setdefault("root_path", str(project_root))
                write_result.metadata.setdefault("delegation", delegation)
                return write_result
            delegation.append(
                {
                    "task_id": task.task_id,
                    "worker_type": task.worker_type,
                    "worker_name": task.worker_name,
                    "file": filename,
                    "status": task.status,
                }
            )

        register_result = self.project_registry.register(
                ProjectRecord(
                project_id=project_id,
                name=title,
                root_path=str(project_root),
                stack="html + css + javascript",
                test_command=[sys.executable, "run_project.py", "--check"],
                run_command=[sys.executable, "run_project.py"],
                important_files=["index.html", "styles.css", "app.js", "README.md", "PLAN.md", "run_project.py"],
            )
        )
        if not register_result.ok:
            register_result.metadata.setdefault("project_id", project_id)
            register_result.metadata.setdefault("name", title)
            register_result.metadata.setdefault("root_path", str(project_root))
            register_result.metadata.setdefault("delegation", delegation)
            return register_result

        return SamResult(
            status="success",
            summary=(
                f"Mason scaffolded {title} at {project_root}. "
                f"Created index.html, styles.css, app.js, README.md, PLAN.md, and run_project.py."
            ),
            next_action="stop",
            metadata={
                "project_id": project_id,
                "name": title,
                "root_path": str(project_root),
                "stack": "html + css + javascript",
                "run_command": [sys.executable, "run_project.py"],
                "important_files": ["index.html", "styles.css", "app.js", "README.md", "PLAN.md", "run_project.py"],
                "delegation": delegation,
            },
        )

    @staticmethod
    def _slugify(text: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
        return normalized or "sam_project"
