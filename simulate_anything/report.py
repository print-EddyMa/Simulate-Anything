"""Self-contained HTML reports: the web UI's renderer with the data baked in."""
from __future__ import annotations

import json
from pathlib import Path

PAGE = Path(__file__).parent / "web" / "index.html"


def to_html(results, answer: str = "", problem: str = "") -> str:
    """One page showing one or more Result objects (and the agent's answer)."""
    if not isinstance(results, (list, tuple)):
        results = [results]
    data = {"results": [r.to_dict() if hasattr(r, "to_dict") else r for r in results],
            "answer": answer, "problem": problem}
    blob = json.dumps(data).replace("</", "<\\/")
    page = PAGE.read_text(encoding="utf-8")
    return page.replace("<!--STATIC-DATA-->",
                        f"<script>window.__STATIC__ = {blob};</script>", 1)


def save_html(path, results, answer: str = "", problem: str = "") -> Path:
    path = Path(path)
    path.write_text(to_html(results, answer, problem), encoding="utf-8")
    return path
