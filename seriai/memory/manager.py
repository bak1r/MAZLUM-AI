"""
Memory manager for MAZLUM.
Clean, minimal, strict write policy.
No legal-first memory. No bloated persona storage.

Memory categories:
- organization_facts: company/org info
- people_roles: who does what
- process_notes: how things work
- customer_facts: customer-related facts
- product_service_facts: products/services info
- operational_rules: business rules
- engineering_notes: technical notes
- support_patterns: common issues and solutions
- session_summary: rolling summary of current session
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("seriai.memory")

VALID_CATEGORIES = [
    "organization_facts",
    "people_roles",
    "process_notes",
    "customer_facts",
    "product_service_facts",
    "operational_rules",
    "engineering_notes",
    "support_patterns",
    "session_summary",
]


class MemoryManager:
    """
    Structured fact storage.
    Strict write gate: only verified, reusable facts get saved.
    """

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._store: dict[str, list] = {cat: [] for cat in VALID_CATEGORIES}
        self._dirty = False
        self._load()

    def _load(self):
        """Load all memory from disk."""
        store_path = self.memory_dir / "facts.json"
        if store_path.exists():
            try:
                data = json.loads(store_path.read_text(encoding="utf-8"))
                for cat in VALID_CATEGORIES:
                    val = data.get(cat, [])
                    # Type validation — list olmalı
                    if not isinstance(val, list):
                        log.warning(f"Memory category '{cat}' is not a list, resetting")
                        val = []
                    self._store[cat] = val
                log.info(f"Memory loaded: {sum(len(v) for v in self._store.values())} facts")
            except Exception as e:
                log.error(f"Memory load failed: {e}")
                # Corrupt dosyayı yedekle — veri kaybını önle
                backup = store_path.with_suffix(".json.corrupt")
                try:
                    store_path.rename(backup)
                    log.warning(f"Corrupt memory file backed up: {backup}")
                except Exception:
                    pass

    def save(self):
        """Persist memory to disk (atomic write)."""
        if not self._dirty:
            return
        store_path = self.memory_dir / "facts.json"
        tmp_path = store_path.with_suffix(".json.tmp")
        try:
            # Atomic: önce temp'e yaz, sonra rename
            tmp_path.write_text(
                json.dumps(self._store, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(store_path)  # Atomic on POSIX
            self._dirty = False
            log.debug("Memory saved.")
        except Exception as e:
            log.error(f"Memory save failed: {e}")
            # Cleanup temp
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def add_fact(self, category: str, fact: str, source: str = "user") -> bool:
        """
        Add a fact to memory. Strict gate:
        - Category must be valid
        - Fact must be non-empty
        - No duplicates
        """
        if category not in VALID_CATEGORIES:
            log.warning(f"Invalid memory category: {category}")
            return False
        if not fact or len(fact.strip()) < 5:
            return False

        # Dedup
        existing_texts = [f.get("text", "") for f in self._store[category]]
        if fact.strip() in existing_texts:
            return False

        self._store[category].append({
            "text": fact.strip(),
            "source": source,
            "ts": int(time.time()),
        })
        self._dirty = True

        # Auto-save
        if len(self._store[category]) % 5 == 0:
            self.save()

        return True

    def get_context(self, context_type: str) -> str:
        """
        Get relevant context for a domain need.
        Maps context_needs to memory categories.
        """
        _CONTEXT_MAP = {
            "db_schema": ["organization_facts"],
            "crm_semantics": ["customer_facts", "product_service_facts"],
            "support_patterns": ["support_patterns"],
            "org_knowledge": ["organization_facts", "people_roles", "process_notes"],
            "engineering_notes": ["engineering_notes"],
            "process_knowledge": ["process_notes", "operational_rules"],
            "legal_knowledge": [],  # handled by optional_legal_pack
        }

        categories = _CONTEXT_MAP.get(context_type, [])
        if not categories:
            return ""

        parts = []
        for cat in categories:
            facts = self._store.get(cat, [])
            if facts:
                # Take last 10 facts per category (most recent)
                recent = facts[-10:]
                lines = [f.get("text", "") for f in recent]
                parts.append(f"[{cat}]\n" + "\n".join(f"- {l}" for l in lines))

        return "\n\n".join(parts)

    def get_facts(self, category: str) -> list:
        """Get all facts in a category."""
        return self._store.get(category, [])

    def remove_fact(self, category: str, index: int) -> bool:
        """Remove a fact by index."""
        facts = self._store.get(category, [])
        if 0 <= index < len(facts):
            facts.pop(index)
            self._dirty = True
            return True
        return False

    def clear_category(self, category: str):
        """Clear all facts in a category."""
        if category in self._store:
            self._store[category] = []
            self._dirty = True

    def clear_all(self):
        """Clear all memory. Use with caution."""
        self._store = {cat: [] for cat in VALID_CATEGORIES}
        self._dirty = True
        self.save()

    def stats(self) -> dict:
        """Return memory statistics."""
        return {cat: len(facts) for cat, facts in self._store.items() if facts}

    def export_memory(self, export_path: Path) -> bool:
        """Export all memory to a portable JSON file."""
        try:
            data = {
                "version": 1,
                "exported_at": int(time.time()),
                "categories": self._store,
            }
            export_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            total = sum(len(v) for v in self._store.values())
            log.info(f"Memory exported: {total} facts → {export_path}")
            return True
        except Exception as e:
            log.error(f"Memory export failed: {e}")
            return False

    def import_memory(self, import_path: Path, merge: bool = True) -> int:
        """
        Import memory from a portable JSON file.
        merge=True: adds new facts to existing (dedup).
        merge=False: replaces everything.
        Returns number of facts imported.
        """
        try:
            data = json.loads(import_path.read_text(encoding="utf-8"))
            categories = data.get("categories", data)  # v1 format or raw

            if not merge:
                self._store = {cat: [] for cat in VALID_CATEGORIES}

            imported = 0
            for cat in VALID_CATEGORIES:
                new_facts = categories.get(cat, [])
                if not isinstance(new_facts, list):
                    continue
                existing_texts = {f.get("text", "") for f in self._store[cat]}
                for fact in new_facts:
                    text = fact.get("text", "") if isinstance(fact, dict) else str(fact)
                    if text and text not in existing_texts:
                        self._store[cat].append(
                            fact if isinstance(fact, dict)
                            else {"text": text, "source": "import", "ts": int(time.time())}
                        )
                        existing_texts.add(text)
                        imported += 1

            self._dirty = True
            self.save()
            log.info(f"Memory imported: {imported} new facts from {import_path}")
            return imported
        except Exception as e:
            log.error(f"Memory import failed: {e}")
            return 0
