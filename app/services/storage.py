"""Local JSON result storage.

One file per processed post: ``data/results/<YYYY-MM-DD>_post_<reddit_id>.json``.
Swap this class for a PostgreSQL repository later; the pipeline only uses
``save`` and ``exists``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from app.models.schemas import ProcessingResult

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"[^A-Za-z0-9_]")


class JsonResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    @staticmethod
    def _safe_id(post_id: str) -> str:
        return _SAFE_ID.sub("_", post_id)

    def path_for(self, result: ProcessingResult) -> Path:
        day = result.processed_at.strftime("%Y-%m-%d")
        return self.results_dir / f"{day}_post_{self._safe_id(result.reddit_post.id)}.json"

    def exists(self, post_id: str) -> bool:
        """True if a COMPLETED result for this post already exists (any date)."""
        if not self.results_dir.exists():
            return False
        for path in self.results_dir.glob(f"*_post_{self._safe_id(post_id)}.json"):
            try:
                if ProcessingResult.model_validate_json(path.read_text(encoding="utf-8")).processing_status == "COMPLETED":
                    return True
            except (OSError, ValueError):
                continue
        return False

    def save(self, result: ProcessingResult) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(result)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on the same volume
        logger.info("Result saved", extra={"post_id": result.reddit_post.id, "path": str(path)})
        return path
