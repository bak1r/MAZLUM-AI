"""
Shared constants for cognition module.
Single source of truth — brain.py ve router.py buradan okur.
"""

# Domain'ler: Bu domain'lerdeki istekler MUTLAKA Sonnet 4 ile işlenir
TOOL_DOMAINS = frozenset({
    "crm", "support", "hr", "operations",
    "engineering", "legal", "desktop",
})
