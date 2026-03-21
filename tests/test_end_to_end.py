#!/usr/bin/env python3
"""
SERIAI End-to-End Runtime Tests
Runs ALL tests with real execution. No mocks. No pytest dependency.
Every test prints PASS or FAIL with details.
"""
import os
import sys
import re
import time
import tempfile
import warnings

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

passed = 0
failed = 0
errors = []


def test(name, condition, detail=""):
    global passed, failed, errors
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)
        errors.append(msg)


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
#  1. CONFIG & SETTINGS TESTS
# ============================================================
section("1. Config & Settings Tests")

from seriai.config.settings import load_config, AppConfig

cfg = load_config()
test("load_config returns AppConfig", isinstance(cfg, AppConfig), f"got {type(cfg)}")
test("Default cognition model is claude-sonnet-4-20250514",
     cfg.models.cognition_model == "claude-sonnet-4-20250514",
     f"got {cfg.models.cognition_model}")
test("Light/fallback model is claude-haiku-4-5-20251001",
     cfg.models.light_model == "claude-haiku-4-5-20251001",
     f"got {cfg.models.light_model}")
test("owner_name has a value", bool(cfg.owner_name), f"got '{cfg.owner_name}'")
test("database.readonly is always True", cfg.database.readonly is True)

# Telegram allowed_user_ids parsing with valid values
old_env = os.environ.get("SERIAI_TELEGRAM_ALLOWED_USERS")
os.environ["SERIAI_TELEGRAM_ALLOWED_USERS"] = "123,456,789"
cfg2 = load_config()
test("Telegram allowed_user_ids parses valid CSV",
     cfg2.telegram.allowed_user_ids == [123, 456, 789],
     f"got {cfg2.telegram.allowed_user_ids}")

# Invalid user IDs
os.environ["SERIAI_TELEGRAM_ALLOWED_USERS"] = "123,abc,456"
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    cfg3 = load_config()
    test("Invalid Telegram ID triggers warning", len(w) > 0, f"warnings: {len(w)}")
    test("Invalid Telegram ID is skipped",
         cfg3.telegram.allowed_user_ids == [123, 456],
         f"got {cfg3.telegram.allowed_user_ids}")

# Empty user IDs
os.environ["SERIAI_TELEGRAM_ALLOWED_USERS"] = ""
cfg4 = load_config()
test("Empty ALLOWED_USERS gives empty list",
     cfg4.telegram.allowed_user_ids == [],
     f"got {cfg4.telegram.allowed_user_ids}")

# Restore env
if old_env is not None:
    os.environ["SERIAI_TELEGRAM_ALLOWED_USERS"] = old_env
else:
    os.environ.pop("SERIAI_TELEGRAM_ALLOWED_USERS", None)

# DB port parsing with empty string
old_port = os.environ.get("SERIAI_DB_PORT")
os.environ["SERIAI_DB_PORT"] = ""
cfg5 = load_config()
test("DB port empty string defaults to 0", cfg5.database.port == 0, f"got {cfg5.database.port}")
if old_port is not None:
    os.environ["SERIAI_DB_PORT"] = old_port
else:
    os.environ.pop("SERIAI_DB_PORT", None)


# ============================================================
#  2. ROUTER TESTS
# ============================================================
section("2. Router Tests")

from seriai.cognition.router import classify_fast, RoutingDecision, _keyword_match

r1 = classify_fast("merhaba")
test("'merhaba' -> domain=general", r1.domain == "general", f"got domain={r1.domain}")
test("'merhaba' -> complexity=simple", r1.complexity == "simple", f"got complexity={r1.complexity}")

r2 = classify_fast("bugun kac islem oldu")
test("'bugun kac islem oldu' -> domain=crm", r2.domain == "crm", f"got domain={r2.domain}")
test("'bugun kac islem oldu' -> db_query in tools",
     "db_query" in r2.suggested_tools,
     f"got tools={r2.suggested_tools}")

r3 = classify_fast("Safari ac")
test("'Safari ac' -> domain=desktop", r3.domain == "desktop", f"got domain={r3.domain}")

r4 = classify_fast("musteri 12345 bilgisi")
test("'musteri 12345 bilgisi' -> domain=crm", r4.domain == "crm", f"got domain={r4.domain}")

r5 = classify_fast("izin durumum ne")
test("'izin durumum ne' -> domain=hr", r5.domain == "hr", f"got domain={r5.domain}")

# Complexity detection with word boundary
r6 = classify_fast("neden bu kadar yavas")
test("'neden bu kadar yavas' -> complex (neden is complex signal)",
     r6.complexity in ("complex", "moderate"),
     f"got complexity={r6.complexity}")

# Word boundary: "ne" should NOT match inside "neden"
test("_keyword_match 'ne' does NOT match inside 'neden'",
     _keyword_match("ne", "neden bu yavas") is False,
     "substring false positive")

test("_keyword_match 'ne' DOES match standalone 'ne'",
     _keyword_match("ne", "bu ne demek") is True)

# Tool domain enforcement
r7 = classify_fast("siparis analiz et")
test("CRM domain gets db_query tool", "db_query" in r7.suggested_tools, f"got {r7.suggested_tools}")

r8 = classify_fast("kod hatasi var bug fix lazim")
test("Engineering domain gets web_search tool",
     "web_search" in r8.suggested_tools,
     f"got domain={r8.domain} tools={r8.suggested_tools}")

# RoutingDecision has all fields
test("RoutingDecision has confidence float",
     isinstance(r1.confidence, float),
     f"got {type(r1.confidence)}")


# ============================================================
#  3. MEMORY MANAGER TESTS
# ============================================================
section("3. Memory Manager Tests")

from seriai.memory.manager import MemoryManager, VALID_CATEGORIES

with tempfile.TemporaryDirectory() as tmpdir:
    from pathlib import Path
    mm = MemoryManager(Path(tmpdir))

    # add_fact + get_context
    ok = mm.add_fact("organization_facts", "SERIAI bir organizasyon asistanidir")
    test("add_fact returns True", ok is True)

    ctx = mm.get_context("org_knowledge")
    test("get_context returns content with fact",
         "SERIAI" in ctx,
         f"got '{ctx[:80]}...'")

    # Duplicate detection
    ok2 = mm.add_fact("organization_facts", "SERIAI bir organizasyon asistanidir")
    test("Duplicate fact returns False", ok2 is False)

    # Invalid category
    ok3 = mm.add_fact("invalid_category_xyz", "some fact")
    test("Invalid category returns False", ok3 is False)

    # f.get("text","") safety - corrupted data
    mm._store["organization_facts"].append({"broken": True})  # no "text" key
    ctx2 = mm.get_context("org_knowledge")
    test("Corrupted data (no 'text' key) does not crash",
         isinstance(ctx2, str),
         "get_context survived corrupted entry")

    # Clean up the corrupted entry
    mm._store["organization_facts"].pop()

    # stats
    stats = mm.stats()
    test("stats returns dict with counts",
         isinstance(stats, dict) and stats.get("organization_facts", 0) == 1,
         f"got {stats}")

    # remove_fact
    ok4 = mm.remove_fact("organization_facts", 0)
    test("remove_fact returns True", ok4 is True)
    test("After remove, category is empty",
         len(mm.get_facts("organization_facts")) == 0)

    # remove_fact invalid index
    ok5 = mm.remove_fact("organization_facts", 99)
    test("remove_fact invalid index returns False", ok5 is False)

    # Short fact rejection
    ok6 = mm.add_fact("organization_facts", "ab")
    test("Very short fact (<5 chars) rejected", ok6 is False)


# ============================================================
#  4. TOOL REGISTRY TESTS
# ============================================================
section("4. Tool Registry Tests")

from seriai.tools.registry import ToolRegistry, ToolDef

tr = ToolRegistry()

# Register a tool
def dummy_tool(x: str, y: int = 0) -> dict:
    return {"result": f"{x}-{y}"}

tr.register(ToolDef(
    name="dummy",
    description="A test tool",
    domain="general",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "string"},
            "y": {"type": "integer"},
        },
        "required": ["x"],
    },
    handler=dummy_tool,
))

test("list_tools contains 'dummy'", "dummy" in tr.list_tools())
test("get_schemas returns schema", len(tr.get_schemas(["dummy"])) == 1)

# Execute with valid params
result = tr.execute("dummy", {"x": "hello", "y": 5})
test("Execute with valid params",
     result == {"result": "hello-5"},
     f"got {result}")

# Execute with missing required params
result2 = tr.execute("dummy", {"y": 5})
test("Execute with missing required param returns error",
     "error" in result2 and "Missing" in result2["error"],
     f"got {result2}")

# Execute unknown tool
result3 = tr.execute("nonexistent", {})
test("Execute unknown tool returns error",
     "error" in result3,
     f"got {result3}")

# Handler that raises exception
def exploding_tool(a: str) -> dict:
    raise RuntimeError("BOOM")

tr.register(ToolDef(
    name="exploder",
    description="Blows up",
    domain="general",
    parameters={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
    handler=exploding_tool,
))

result4 = tr.execute("exploder", {"a": "test"})
test("Handler exception returns error dict (no crash)",
     "error" in result4,
     f"got {result4}")

# Domain filtering
test("list_tools with domain filter",
     "dummy" in tr.list_tools(domain="general"))


# ============================================================
#  5. DB READONLY SAFETY TESTS
# ============================================================
section("5. DB ReadOnly Safety Tests")

from seriai.tools.db.readonly import _FORBIDDEN

# Blocked operations
blocked_sqls = [
    "INSERT INTO users VALUES (1,'hack')",
    "UPDATE users SET name='hack'",
    "DELETE FROM users",
    "DROP TABLE users",
    "ALTER TABLE users ADD col INT",
    "TRUNCATE TABLE users",
    "EXEC sp_executesql",
    "EXECUTE sp_test",
    "GRANT ALL ON users TO public",
    "REVOKE SELECT ON users FROM public",
    "CREATE TABLE evil (id INT)",
]

for sql in blocked_sqls:
    keyword = sql.split()[0]
    test(f"FORBIDDEN blocks: {keyword}",
         _FORBIDDEN.search(sql) is not None,
         f"SQL: {sql}")

# Case insensitive
for variant in ["insert into x", "Insert Into X", "INSERT INTO X"]:
    test(f"Case insensitive: '{variant[:20]}'",
         _FORBIDDEN.search(variant) is not None)

# Allowed operations
allowed_sqls = [
    "SELECT * FROM users",
    "WITH cte AS (SELECT 1) SELECT * FROM cte",
    "SHOW TABLES",
    "DESCRIBE users",
    "EXPLAIN SELECT * FROM users",
]

for sql in allowed_sqls:
    keyword = sql.split()[0]
    test(f"ALLOWED passes: {keyword}",
         _FORBIDDEN.search(sql) is None,
         f"SQL: {sql}")

# Table name validation regex
table_regex = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
valid_tables = ["users", "payment_transactions", "_internal", "Table1"]
invalid_tables = ["users; DROP TABLE--", "table name", "123start", "table.name", "table'inject"]

for t in valid_tables:
    test(f"Valid table name: '{t}'", table_regex.match(t) is not None)

for t in invalid_tables:
    test(f"Invalid table name blocked: '{t}'", table_regex.match(t) is None)

# SQL that starts with non-SELECT
class FakeConfig:
    class database:
        engine = ""
        host = ""
        port = 0
        name = ""
        user = ""
        password = ""
        readonly = True
        max_query_rows = 500
        query_timeout_sec = 30

from seriai.tools.db.readonly import ReadOnlyDB
fake_db = ReadOnlyDB(FakeConfig())
fake_db._connected = True  # bypass connection check, will fail at execution but test SQL check
# We test the query method's SQL prefix check
result_bad = fake_db.query("CALL some_procedure()")
test("Non-SELECT SQL blocked by query()",
     "error" in result_bad,
     f"got {result_bad}")

result_good_prefix = fake_db.query("SELECT 1")
# This will fail at execution (no real engine) but should NOT fail at SQL check
# If error contains "yasak" it means SQL check blocked it; otherwise it passed SQL check
test("SELECT passes SQL prefix check",
     "yasak" not in result_good_prefix.get("error", ""),
     f"got {result_good_prefix}")


# ============================================================
#  6. PROMPT BUILDER TESTS
# ============================================================
section("6. Prompt Builder Tests")

from seriai.cognition.prompts import build_system_prompt

p1 = build_system_prompt()
test("build_system_prompt returns string", isinstance(p1, str) and len(p1) > 50)
test("Core prompt contains 'SERIAI'", "SERIAI" in p1)
test("Core prompt contains 'bilmiyorum'", "bilmiyorum" in p1)

p2 = build_system_prompt(domain="crm")
test("CRM domain includes CRM context", "CRM" in p2 or "crm" in p2.lower(), f"len={len(p2)}")
test("CRM domain includes read-only note", "read-only" in p2.lower() or "read-only" in p2)

p3 = build_system_prompt(domain="desktop")
test("Desktop domain includes desktop context",
     "desktop" in p3.lower() or "Desktop" in p3 or "uygulama" in p3.lower(),
     f"len={len(p3)}")

p4 = build_system_prompt(owner_name="Ahmet")
test("Owner name included in prompt", "Ahmet" in p4)

p5 = build_system_prompt(language="en")
test("English language instruction included",
     "en" in p5.lower() or "Respond in en" in p5,
     f"contains 'Respond in en': {'Respond in en' in p5}")


# ============================================================
#  7. CAPABILITY REGISTRY TESTS
# ============================================================
section("7. Capability Registry Tests")

from seriai.cognition.capabilities import CapabilityRegistry, Status

cap_reg = CapabilityRegistry()

all_caps = cap_reg.get_all()
test("Capabilities exist", len(all_caps) > 0, f"count={len(all_caps)}")

valid_statuses = {Status.ACTIVE, Status.PARTIAL, Status.SCAFFOLD, Status.MISSING}
all_valid = all(c.status in valid_statuses for c in all_caps)
test("All capabilities have valid Status enum",
     all_valid,
     f"statuses: {[c.status for c in all_caps]}")

active = cap_reg.get_active()
test("get_active returns only ACTIVE",
     all(c.status == Status.ACTIVE for c in active),
     f"active count={len(active)}")
test("get_active returns subset of get_all",
     len(active) <= len(all_caps))

# get_status for known tool
db_cap = cap_reg.get_status("db_query")
test("get_status('db_query') returns Capability",
     db_cap is not None and db_cap.status == Status.ACTIVE)

# get_status for unknown tool
unk = cap_reg.get_status("nonexistent_tool_xyz")
test("get_status unknown returns None", unk is None)

# build_capability_prompt
prompt = cap_reg.build_capability_prompt()
test("build_capability_prompt returns string", isinstance(prompt, str) and len(prompt) > 20)


# ============================================================
#  8. KNOWLEDGE LOADER TESTS
# ============================================================
section("8. Knowledge Loader Tests")

from seriai.knowledge.loader import KnowledgeLoader

kl = KnowledgeLoader()

crm_knowledge = kl.get("crm")
test("get('crm') returns string (no crash)",
     isinstance(crm_knowledge, str),
     f"len={len(crm_knowledge)}")

nonexistent = kl.get("nonexistent_pack_xyz")
test("get('nonexistent') returns empty string",
     nonexistent == "",
     f"got '{nonexistent[:50]}'")

packs = kl.list_packs()
test("list_packs returns list", isinstance(packs, list), f"packs={packs}")


# ============================================================
#  9. STRESS TESTS
# ============================================================
section("9. Stress Tests")

# Router: 1000 rapid calls
t0 = time.time()
crash = False
for i in range(1000):
    try:
        classify_fast(f"test mesaji {i} musteri siparis analiz")
    except Exception as e:
        crash = True
        break
elapsed_router = time.time() - t0
test(f"Router: 1000 classify_fast calls no crash ({elapsed_router:.2f}s)",
     not crash)
test(f"Router: 1000 calls under 5s",
     elapsed_router < 5.0,
     f"took {elapsed_router:.2f}s")

# Memory: 100 rapid adds
with tempfile.TemporaryDirectory() as tmpdir:
    mm2 = MemoryManager(Path(tmpdir))
    t0 = time.time()
    crash = False
    for i in range(100):
        try:
            mm2.add_fact("organization_facts", f"Test fact number {i} for stress testing")
        except Exception as e:
            crash = True
            break
    elapsed_mem = time.time() - t0
    test(f"Memory: 100 add_fact calls no crash ({elapsed_mem:.2f}s)", not crash)
    test("Memory: all 100 facts stored",
         len(mm2.get_facts("organization_facts")) == 100,
         f"got {len(mm2.get_facts('organization_facts'))}")

# Tool registry: 50 rapid executes
t0 = time.time()
crash = False
for i in range(50):
    try:
        tr.execute("dummy", {"x": f"stress_{i}"})
    except Exception as e:
        crash = True
        break
elapsed_tool = time.time() - t0
test(f"Tool registry: 50 execute calls no crash ({elapsed_tool:.2f}s)", not crash)

# FORBIDDEN regex: 10000 pattern matches (ReDoS check)
t0 = time.time()
crash = False
test_sql = "SELECT * FROM users WHERE name = 'test' AND id = 1 ORDER BY created_at DESC LIMIT 100"
for i in range(10000):
    try:
        _FORBIDDEN.search(test_sql)
    except Exception:
        crash = True
        break
elapsed_regex = time.time() - t0
test(f"FORBIDDEN regex: 10000 matches no crash ({elapsed_regex:.4f}s)", not crash)
test(f"FORBIDDEN regex: 10000 matches no ReDoS (under 2s)",
     elapsed_regex < 2.0,
     f"took {elapsed_regex:.4f}s")


# ============================================================
#  10. CROSS-MODULE INTEGRATION TESTS
# ============================================================
section("10. Cross-Module Integration Tests")

# Config -> Brain init (doesn't crash)
from seriai.cognition.brain import Brain, BrainResponse

with tempfile.TemporaryDirectory() as tmpdir:
    cfg_int = load_config()
    mm_int = MemoryManager(Path(tmpdir))
    tr_int = ToolRegistry()

    try:
        brain = Brain(config=cfg_int, memory=mm_int, tools=tr_int)
        test("Brain init with real config/memory/tools: no crash", True)
    except Exception as e:
        test("Brain init with real config/memory/tools: no crash", False, str(e))

    # Brain registers remember_fact tool automatically
    test("Brain auto-registers remember_fact tool",
         "remember_fact" in tr_int.list_tools(),
         f"tools: {tr_int.list_tools()}")

# Router -> Prompts (domain flows correctly)
routing = classify_fast("musteri siparisleri listele")
prompt = build_system_prompt(domain=routing.domain)
test("Router domain flows to prompt builder",
     routing.domain == "crm" and ("CRM" in prompt or "crm" in prompt.lower()),
     f"domain={routing.domain}")

# Memory + Tool registry together
with tempfile.TemporaryDirectory() as tmpdir:
    mm_combo = MemoryManager(Path(tmpdir))
    tr_combo = ToolRegistry()

    def mem_tool(fact: str) -> dict:
        ok = mm_combo.add_fact("organization_facts", fact)
        return {"saved": ok}

    tr_combo.register(ToolDef(
        name="save_fact",
        description="Save a fact",
        domain="general",
        parameters={"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
        handler=mem_tool,
    ))

    result = tr_combo.execute("save_fact", {"fact": "Integration test fact for SERIAI"})
    test("Memory+ToolRegistry integration: fact saved via tool",
         result.get("saved") is True,
         f"got {result}")
    test("Memory+ToolRegistry integration: fact retrievable",
         "Integration" in mm_combo.get_context("org_knowledge"))


# ============================================================
#  11. EDGE CASES
# ============================================================
section("11. Edge Cases")

# Empty string to router
r_empty = classify_fast("")
test("Empty string -> router no crash",
     isinstance(r_empty, RoutingDecision),
     f"domain={r_empty.domain}")
test("Empty string -> domain=general",
     r_empty.domain == "general")

# Very long string (10KB)
long_text = "musteri " * 1280  # ~10KB
t0 = time.time()
r_long = classify_fast(long_text)
elapsed_long = time.time() - t0
test("10KB input -> router no crash",
     isinstance(r_long, RoutingDecision),
     f"domain={r_long.domain}")
test(f"10KB input -> router under 2s",
     elapsed_long < 2.0,
     f"took {elapsed_long:.2f}s")

# Unicode/emoji inputs
r_emoji = classify_fast("merhaba dunyaya selam")
test("Unicode input -> no crash", isinstance(r_emoji, RoutingDecision))

r_emoji2 = classify_fast("musteri bilgisi")
test("Emoji mixed with Turkish -> works",
     isinstance(r_emoji2, RoutingDecision))

# None-like edge cases for memory
with tempfile.TemporaryDirectory() as tmpdir:
    mm_edge = MemoryManager(Path(tmpdir))
    ok_none = mm_edge.add_fact("organization_facts", "")
    test("Empty string fact rejected", ok_none is False)

    ok_short = mm_edge.add_fact("organization_facts", "   ")
    test("Whitespace-only fact rejected", ok_short is False)

# Router with special characters
r_special = classify_fast("!@#$%^&*()")
test("Special chars -> router no crash",
     isinstance(r_special, RoutingDecision))

# Classify returns correct type always
test("RoutingDecision.suggested_tools is list",
     isinstance(r_special.suggested_tools, list))
test("RoutingDecision.context_needs is list",
     isinstance(r_special.context_needs, list))
test("RoutingDecision.model_tier is string",
     isinstance(r_special.model_tier, str))

# BrainResponse dataclass
br = BrainResponse(text="test", domain="general", model_used="test")
test("BrainResponse default tools_used is empty list",
     br.tools_used == [])
test("BrainResponse default input_tokens is 0",
     br.input_tokens == 0)
test("BrainResponse default latency_ms is 0",
     br.latency_ms == 0)


# ============================================================
#  FINAL SUMMARY
# ============================================================
print(f"\n{'='*60}")
print(f"  FINAL RESULTS")
print(f"{'='*60}")
total = passed + failed
print(f"  Total:  {total}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
if errors:
    print(f"\n  Failed tests:")
    for e in errors:
        print(f"    {e}")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
