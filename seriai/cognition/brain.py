"""
MAZLUM Brain - Main orchestrator.
Receives a user message, routes it, executes tools, returns response.

Flow:
1. Request arrives (text from any interface)
2. Fast classify (keyword-based, no LLM)
3. FAST PATH: instant task? → execute directly, no LLM (< 100ms)
4. LIGHT PATH: simple chat? → Haiku 4.5 minimal prompt
5. HEAVY PATH: complex/tool task → Sonnet 4 with full context
6. If tool_use → execute tools → feed results back
7. Generate final answer
8. Memory write gate check
9. Return response
"""
import logging
import re
import time
import threading
from typing import Optional
from dataclasses import dataclass, field

from seriai.config.settings import AppConfig
from seriai.config.providers import get_provider, LLMResponse
from seriai.cognition.router import classify_fast, RoutingDecision
from seriai.cognition.prompts import build_system_prompt, build_domain_context
from seriai.cognition.capabilities import CapabilityRegistry
from seriai.memory.manager import MemoryManager
from seriai.tools.registry import ToolRegistry

log = logging.getLogger("seriai.cognition.brain")

MAX_TOOL_ROUNDS = 4


def _turkish_lower(text: str) -> str:
    """Turkish-aware lowercase."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _extract_dir(text: str) -> str:
    """Extract directory shortcut from text."""
    t = _turkish_lower(text)
    if "download" in t or "indirilen" in t:
        return "downloads"
    if "belge" in t or "document" in t:
        return "documents"
    if "resim" in t or "picture" in t:
        return "pictures"
    return "desktop"
MAX_TOOL_RESULT_CHARS = 4096


@dataclass
class BrainResponse:
    """Response from the brain."""
    text: str
    domain: str
    model_used: str
    tools_used: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class Brain:
    """Main MAZLUM orchestrator."""

    def __init__(self, config: AppConfig, memory: MemoryManager, tools: ToolRegistry):
        self.config = config
        self.memory = memory
        self.tools = tools
        self.capabilities = CapabilityRegistry()
        self._conversations: dict[str, list] = {}  # per-source conversation history
        self._conv_lock = threading.Lock()
        self._max_history = 10         # last N turns kept

        # Register memory tool — model konuşmadan öğrendiğini kaydedebilir
        self._register_memory_tool()

    # ── FAST PATH: Instant task patterns (no LLM needed) ──────────
    _INSTANT_PATTERNS = [
        # URL aç — MUST be checked BEFORE "X aç" to avoid treating URL as app name
        (re.compile(r"(https?://\S+)", re.I), "open_url", lambda m: {"url": m.group(1)}),
        # Ekran görüntüsü — before generic patterns
        (re.compile(r"ekran\s*g[öo]r[üu]nt[üu]s[üu]", re.I), "computer_settings", lambda m: {"action": "screenshot"}),
        (re.compile(r"screenshot", re.I), "computer_settings", lambda m: {"action": "screenshot"}),
        # Ses kontrol
        (re.compile(r"ses(?:i)?\s*(aç|art[ıi]r|yükselt|yukari)", re.I), "computer_settings", lambda m: {"action": "volume_up"}),
        (re.compile(r"ses(?:i)?\s*(k[ıi]s|azalt|d[üu][şs][üu]r|a[şs]a[gğ][ıi])", re.I), "computer_settings", lambda m: {"action": "volume_down"}),
        (re.compile(r"ses(?:i)?\s*kapat|mute|sustur", re.I), "computer_settings", lambda m: {"action": "mute"}),
        (re.compile(r"ses\s*seviyesi|volume", re.I), "computer_settings", lambda m: {"action": "get_volume"}),
        # Dosya listeleme
        (re.compile(r"(masa[üu]st[üu]|desktop|downloads?|indirilenler|belgeler|documents?).*(dosya|listele|g[öo]ster)", re.I),
         "list_files", lambda m: {"directory": _extract_dir(m.group(0))}),
        (re.compile(r"dosyalar[ıi]?\s*(listele|g[öo]ster)", re.I), "list_files", lambda m: {"directory": "desktop"}),
        # "X aç" / "X'i aç" — extract app name, keep FULL word before "aç"
        (re.compile(r"^(.+?)\s+aç$", re.I), "open_app", lambda m: {"app_name": m.group(1).strip().rstrip("'ıiuü")}),
        (re.compile(r"^(.+?)['\u2019][ıiuü]\s+aç$", re.I), "open_app", lambda m: {"app_name": m.group(1).strip()}),
        (re.compile(r"^aç\s+(.+)$", re.I), "open_app", lambda m: {"app_name": m.group(1).strip()}),
        # "X kapat"
        (re.compile(r"^(.+?)\s+kapat$", re.I), "close_app", lambda m: {"app_name": m.group(1).strip().rstrip("'ıiuü")}),
        (re.compile(r"^(.+?)['\u2019][ıiuü]\s+kapat$", re.I), "close_app", lambda m: {"app_name": m.group(1).strip()}),
        (re.compile(r"^kapat\s+(.+)$", re.I), "close_app", lambda m: {"app_name": m.group(1).strip()}),
    ]

    def _try_fast_path(self, text: str) -> Optional[BrainResponse]:
        """
        Instant tasks — direct tool execution, ZERO LLM calls.
        Target: < 100ms for desktop commands.
        """
        text_stripped = text.strip()

        for pattern, tool_name, param_fn in self._INSTANT_PATTERNS:
            match = pattern.search(text_stripped)
            if match:
                if not self.tools._tools.get(tool_name):
                    log.debug(f"Fast path tool '{tool_name}' not registered, skipping.")
                    continue

                params = param_fn(match)
                t0 = time.time()
                result = self.tools.execute(tool_name, params)
                elapsed = int((time.time() - t0) * 1000)

                # Build response text from tool result
                if isinstance(result, dict):
                    if "files" in result:
                        # list_files — human-readable format
                        files = result["files"]
                        count = result.get("count", len(files))
                        directory = result.get("directory", "")
                        lines = [f"{directory} — {count} dosya:"]
                        for f in files[:20]:
                            icon = "📁" if f.get("type") == "dir" else "📄"
                            lines.append(f"  {icon} {f['name']}")
                        if count > 20:
                            lines.append(f"  ... ve {count - 20} dosya daha")
                        resp_text = "\n".join(lines)
                    else:
                        resp_text = result.get("result", result.get("error", str(result)))
                else:
                    resp_text = str(result)

                log.info(f"FAST PATH: {tool_name}({params}) → {elapsed}ms")
                return BrainResponse(
                    text=resp_text,
                    domain="desktop",
                    model_used="fast-path",
                    tools_used=[tool_name],
                    latency_ms=elapsed,
                )
        return None

    def process(self, user_text: str, context: dict = None) -> BrainResponse:
        """
        Process a user message end-to-end.
        context: optional metadata (source=telegram/chat/web, user_id, etc.)
        """
        t0 = time.time()
        context = context or {}

        # Step 0: FAST PATH — instant tasks bypass LLM entirely
        fast = self._try_fast_path(user_text)
        if fast:
            return fast

        # Step 1: Fast classify
        routing = classify_fast(user_text)
        log.info(f"Routing: domain={routing.domain} intent={routing.intent} "
                 f"complexity={routing.complexity} tier={routing.model_tier}")

        # Step 2: Check if legal domain but legal pack disabled
        if routing.domain == "legal" and not self.config.enable_legal_pack:
            return BrainResponse(
                text="Hukuk modülü şu an aktif değil. Yönetici tarafından etkinleştirilebilir.",
                domain="legal",
                model_used="none",
                latency_ms=int((time.time() - t0) * 1000),
            )

        # Step 3: Select model and provider
        provider_name, model_name, max_tokens = self._select_model(routing)
        provider = get_provider(provider_name)

        # Step 4: Build system prompt — LIGHT for simple chat, FULL for complex
        is_light = (routing.complexity == "simple"
                    and routing.intent == "chat"
                    and routing.domain in ("general", "desktop"))

        system = build_system_prompt(
            domain=routing.domain,
            language=self.config.language,
            owner_name=self.config.owner_name,
        )

        if not is_light:
            # Heavy path: inject domain context + capabilities
            domain_ctx = build_domain_context(routing, self.memory, self.config)
            if domain_ctx:
                system += "\n\n" + domain_ctx
            system += "\n\n" + self.capabilities.build_capability_prompt()

        # Step 5: Build messages — light path skips history for simple greetings
        if is_light:
            messages = [{"role": "user", "content": user_text}]
        else:
            messages = self._build_messages(user_text, context)

        # Step 6: Get available tools for this domain
        # remember_fact her zaman dahil (model konuşmadan öğrendiğini kaydetsin)
        tool_names = list(routing.suggested_tools)
        if not is_light and "remember_fact" not in tool_names:
            tool_names.append("remember_fact")
        tool_schemas = self.tools.get_schemas(tool_names)

        # Step 7: LLM call loop (with tool execution)
        total_in = 0
        total_out = 0
        tools_used = []

        try:
            for round_num in range(MAX_TOOL_ROUNDS):
                resp = provider.chat(
                    messages=messages,
                    model=model_name,
                    system=system,
                    max_tokens=max_tokens,
                    tools=tool_schemas if tool_schemas else None,
                    temperature=0.3 if routing.complexity != "simple" else 0.1,
                )
                total_in += resp.input_tokens
                total_out += resp.output_tokens

                # No tool calls → done
                if not resp.tool_calls:
                    break

                # Execute tools
                tool_results = []
                for tc in resp.tool_calls:
                    try:
                        result = self.tools.execute(tc["name"], tc["input"])
                    except Exception as te:
                        log.error(f"Tool execution error ({tc['name']}): {te}")
                        # Don't leak internal details (paths, passwords, stack traces)
                        result = f"Tool hatası: {tc['name']} çalıştırılamadı."
                    result_text = str(result)
                    if len(result_text) > MAX_TOOL_RESULT_CHARS:
                        result_text = result_text[:MAX_TOOL_RESULT_CHARS] + "\n... (truncated)"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_text,
                    })
                    tools_used.append(tc["name"])

                # Add assistant message with proper content blocks
                if provider_name == "anthropic":
                    raw_content = getattr(resp, 'raw_content', None)
                    if raw_content:
                        messages.append({"role": "assistant", "content": raw_content})
                    else:
                        messages.append({"role": "assistant", "content": resp.text or "[tool_use]"})
                    messages.append({"role": "user", "content": tool_results})
                else:
                    messages.append({"role": "assistant", "content": resp.text or "[tool_use]"})
                    combined = "\n".join(f"[{tr['tool_use_id']}]: {tr['content']}" for tr in tool_results)
                    messages.append({"role": "user", "content": combined})

            # Max rounds exhausted but last response wanted more tools → force final answer
            # resp.text olsa bile yarım cümle olabilir (örn "kontrol edeyim:")
            if resp.tool_calls:
                log.warning(f"Max tool rounds ({MAX_TOOL_ROUNDS}) exhausted, forcing final answer")
                try:
                    # One more call WITHOUT tools to force a text summary
                    final_resp = provider.chat(
                        messages=messages,
                        model=model_name,
                        system=system + "\n\nTool round limiti doldu. Elindeki verilerle KISA ve NET bir özet cevap ver. Yeni tool çağırma.",
                        max_tokens=max_tokens,
                        tools=None,
                        temperature=0.3,
                    )
                    total_in += final_resp.input_tokens
                    total_out += final_resp.output_tokens
                    if final_resp.text:
                        resp = final_resp
                except Exception as fe:
                    log.error(f"Final answer call failed: {fe}")

        except Exception as e:
            # Provider error — return clear error to user, no silent fallback
            log.error(f"LLM error ({type(e).__name__}): {e}")
            elapsed = int((time.time() - t0) * 1000)
            # Kullanıcıya API key veya internal detay sızdırma
            safe_msg = str(e)
            if "api_key" in safe_msg.lower() or "sk-" in safe_msg:
                safe_msg = "AI servisi geçici olarak kullanılamıyor."
            return BrainResponse(
                text=f"Sistem hatası: {safe_msg}",
                domain=routing.domain,
                model_used=model_name,
                tools_used=tools_used,
                input_tokens=total_in,
                output_tokens=total_out,
                latency_ms=elapsed,
            )

        # Step 8: Update conversation history (per-source, thread-safe)
        source = (context or {}).get("source", "cli")
        with self._conv_lock:
            conv = self._conversations.setdefault(source, [])
            conv.append({"role": "user", "content": user_text})
            conv.append({"role": "assistant", "content": resp.text})
            self._trim_history(source)

        # Step 9: Memory write gate
        self._memory_write_gate(user_text, resp.text, routing)

        elapsed = int((time.time() - t0) * 1000)
        return BrainResponse(
            text=resp.text,
            domain=routing.domain,
            model_used=model_name,
            tools_used=tools_used,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=elapsed,
        )

    def _select_model(self, routing: RoutingDecision) -> tuple:
        """Select provider, model, max_tokens based on routing.

        Critical task guard:
        - Tool, DB, CRM, Telegram, iş mantığı → MUTLAKA Sonnet 4
        - Haiku 4.5 SADECE: domain=general AND complexity=simple AND intent=chat AND tool yok
        - Sessiz fallback YASAK
        """
        from seriai.cognition.constants import TOOL_DOMAINS as _TOOL_DOMAINS

        # ── Chat + simple override ──────────────────────────────
        # Even if domain is desktop/crm/etc, if the user is just chatting
        # (no tools needed, simple complexity, chat intent) → use Haiku for speed.
        # This catches false-positive domain classifications like "sesimi duyuyor musun"
        # being routed to desktop.
        is_simple_chat = (
            routing.intent == "chat"
            and routing.complexity == "simple"
            and routing.domain in ("general", "desktop")  # basit sohbet domain'leri
        )
        if is_simple_chat:
            log.info(f"Model: Haiku 4.5 (reason: basit sohbet override, domain={routing.domain} ama tool yok + chat + simple)")
            return (
                self.config.models.light_provider,
                self.config.models.light_model,
                512,  # basit sohbet için 512 token yeter, hız kazanır
            )

        # Critical task guard: tool/domain/intent gerektiriyorsa → Sonnet 4
        needs_sonnet = (
            routing.suggested_tools
            or routing.domain in _TOOL_DOMAINS
            or routing.intent in ("action", "analysis", "query")
        )

        if needs_sonnet:
            # Analiz/rapor isteklerinde daha fazla token ver — kanıtlı uzun cevap gerekebilir
            max_tok = self.config.models.cognition_max_tokens
            if routing.intent == "analysis" or routing.complexity == "complex":
                max_tok = max(max_tok, 8192)
            log.info(f"Model: Sonnet 4 (reason: {'tools=' + str(routing.suggested_tools) if routing.suggested_tools else 'domain=' + routing.domain if routing.domain in _TOOL_DOMAINS else 'intent=' + routing.intent}, max_tokens={max_tok})")
            return (
                self.config.models.cognition_provider,
                self.config.models.cognition_model,
                max_tok,
            )

        # Hafif işler: general + moderate + chat + tool yok
        if (routing.domain == "general"
                and routing.complexity in ("simple", "moderate")
                and routing.intent == "chat"
                and not routing.suggested_tools):
            log.info("Model: Haiku 4.5 (reason: basit sohbet, tool yok)")
            return (
                self.config.models.light_provider,
                self.config.models.light_model,
                512,
            )

        # Default: Sonnet 4 (hiçbir durumda kalite düşürülmez)
        log.info(f"Model: Sonnet 4 (reason: default, complexity={routing.complexity})")
        return (
            self.config.models.cognition_provider,
            self.config.models.cognition_model,
            self.config.models.cognition_max_tokens,
        )

    def _build_messages(self, user_text: str, context: dict = None) -> list:
        """Build message list from conversation history + new message.
        Thread-safe: copies history inside lock before appending new message."""
        source = (context or {}).get("source", "cli")
        with self._conv_lock:
            conv = self._conversations.get(source, [])
            msgs = list(conv)  # copy under lock
            msgs.append({"role": "user", "content": user_text})
        return msgs

    def _trim_history(self, source: str):
        """Keep only last N turns for a source. Caller must hold _conv_lock."""
        max_msgs = self._max_history * 2
        conv = self._conversations.get(source, [])
        if len(conv) > max_msgs:
            self._conversations[source] = conv[-max_msgs:]

    def _memory_write_gate(self, user_text: str, response: str, routing: RoutingDecision):
        """
        Strict write gate: only save genuinely reusable facts.
        NOT every conversation turn. NOT chat/greetings.
        """
        if routing.intent == "chat" or routing.complexity == "simple":
            return
        # In future: use LLM to extract facts worth saving
        # For now: no automatic memory writes
        pass

    def _register_memory_tool(self):
        """Register remember_fact tool — LLM konuşmadan öğrendiğini hafızaya kaydetsin."""
        from seriai.tools.registry import ToolDef
        from seriai.memory.manager import VALID_CATEGORIES

        memory_ref = self.memory  # closure capture

        def remember_fact(category: str, fact: str) -> dict:
            """Öğrenilen bilgiyi kalıcı hafızaya kaydet."""
            if category not in VALID_CATEGORIES:
                return {"error": f"Geçersiz kategori: {category}. Geçerli: {', '.join(VALID_CATEGORIES)}"}
            if not fact or len(fact.strip()) < 10:
                return {"error": "Bilgi çok kısa. En az 10 karakter olmalı."}
            try:
                ok = memory_ref.add_fact(category, fact, source="learned")
            except Exception as mem_err:
                log.error(f"Memory write failed: {mem_err}")
                return {"error": f"Hafıza yazma hatası: {mem_err}"}
            if ok:
                log.info(f"Memory learned: [{category}] {fact[:80]}")
                return {"result": f"Kaydedildi: [{category}] {fact[:80]}..."}
            else:
                return {"result": "Bu bilgi zaten kayıtlı."}

        self.tools.register(ToolDef(
            name="remember_fact",
            description=(
                "Konuşmadan öğrenilen önemli iş bilgisini kalıcı hafızaya kaydet. "
                "Kullanıcı yeni bir iş kuralı, terim, süreç veya bilgi öğrettiğinde çağır. "
                "Sıradan sohbet veya geçici bilgi KAYDETME."
            ),
            domain="general",
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "Kategori: organization_facts (şirket bilgisi), "
                            "operational_rules (iş kuralları), process_notes (süreçler), "
                            "customer_facts (müşteri bilgisi), people_roles (kişi/roller), "
                            "engineering_notes (teknik notlar), support_patterns (destek kalıpları)"
                        ),
                        "enum": VALID_CATEGORIES,
                    },
                    "fact": {
                        "type": "string",
                        "description": "Kaydedilecek bilgi. Net, kısa, tekrar kullanılabilir olmalı.",
                    },
                },
                "required": ["category", "fact"],
            },
            handler=remember_fact,
        ))
        log.debug("Memory tool (remember_fact) registered.")

    def reset_session(self, source: str = None):
        """Clear conversation history."""
        with self._conv_lock:
            if source:
                self._conversations.pop(source, None)
            else:
                self._conversations.clear()
