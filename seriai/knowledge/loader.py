"""
Knowledge pack loader.
Loads domain-specific knowledge files on demand.
No full-stack loading at startup - load only what's needed per request.
"""
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("seriai.knowledge")

PACKS_DIR = Path(__file__).parent / "packs"


class KnowledgeLoader:
    """
    Loads knowledge packs by domain.
    Each pack is a directory with markdown/json files.
    Loaded lazily, cached in memory.
    """

    def __init__(self):
        self._cache: dict[str, str] = {}

    def get(self, pack_name: str) -> str:
        """Get knowledge content for a pack. Returns empty string if not found."""
        if pack_name in self._cache:
            return self._cache[pack_name]

        pack_dir = PACKS_DIR / pack_name
        if not pack_dir.exists():
            return ""

        # Load all .md files in the pack directory
        parts = []
        for md_file in sorted(pack_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                parts.append(content)
            except Exception as e:
                log.warning(f"Failed to load {md_file}: {e}")

        combined = "\n\n".join(parts)
        self._cache[pack_name] = combined
        return combined

    def list_packs(self) -> list[str]:
        """List available knowledge packs."""
        if not PACKS_DIR.exists():
            return []
        return [d.name for d in PACKS_DIR.iterdir() if d.is_dir()]

    def clear_cache(self):
        """Clear the knowledge cache."""
        self._cache.clear()
