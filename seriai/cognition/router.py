"""
Intent router and task classifier.
Determines what domain pack, tools, and model to use for a request.
This is the main "brain" decision layer - keyword-based, no LLM call.
"""
import logging
import re
from typing import Optional
from dataclasses import dataclass

log = logging.getLogger("seriai.cognition.router")


def _keyword_match(keyword: str, text: str) -> bool:
    """Smart keyword matching.

    Multi-word keywords (e.g. 'ses aç', 'otomatik onay') use simple substring.
    Single-word keywords use word-boundary matching so that e.g. 'ses' does NOT
    match inside 'sesimi', 'sesli', 'sesin' etc.
    """
    if " " in keyword or "_" in keyword:
        # Multi-word / phrase → substring is fine
        return keyword in text
    # Single word → word boundary (Turkish-aware: allow ğüşıöç around boundary)
    pattern = r'(?<![a-zA-ZçğıöşüÇĞİÖŞÜ])' + re.escape(keyword) + r'(?![a-zA-ZçğıöşüÇĞİÖŞÜ])'
    return bool(re.search(pattern, text))


@dataclass
class RoutingDecision:
    """Result of intent analysis."""
    domain: str              # general, crm, support, hr, engineering, operations, legal
    intent: str              # query, action, analysis, report, chat
    complexity: str          # simple, moderate, complex
    suggested_tools: list    # tool names to load
    model_tier: str          # fast (Haiku 4.5), standard (Sonnet 4)
    context_needs: list      # what context to inject: db_schema, org_knowledge, etc.
    confidence: float = 0.0


# ── Keyword-based fast classifier (no LLM call needed) ──────────
_DOMAIN_SIGNALS = {
    "crm": [
        "musteri", "müşteri", "siparis", "sipariş", "satis", "satış",
        "fatura", "teklif", "lead", "pipeline", "crm", "firma",
        "iletisim", "iletişim", "kayit", "kayıt",
        "islem", "işlem", "havale", "yatirim", "yatırım", "cekim", "çekim",
        "bekleyen", "onay", "red", "banka", "iban", "hesap", "bakiye",
        "site", "grup", "komisyon", "tutar", "odeme", "ödeme",
        # Callback & dead letter
        "callback", "dead letter", "dead_letter", "retry", "başarısız",
        # Telegram botları
        "bot", "simplesorgu", "sorgu", "uyuşmayan", "imha",
        "banka_limit", "bekleyen_cekim",
        # Otomatik onay/red
        "otomatik onay", "auto approve", "sms eşleştirme", "sms eslestirme",
        "reject_time", "otomatik red",
        # Fraud & güvenlik
        "fraud", "dolandırıcılık", "dolandiricilik", "şüpheli", "bloke",
        # İş terimleri
        "provider", "gateway", "panelsh", "serialhavale", "ekip", "operatör",
        "personal", "ortacı", "ortaci", "takviye", "revert",
        "limit", "rapor", "kasa",
        # DB terimleri — serialhavale veritabanı
        "database", "veritabanı", "veritabani", "db", "tablo", "gecikme",
        "stabilite", "stabil",
    ],
    "support": [
        "destek", "sorun", "ariza", "arıza", "sikayeti", "şikayeti",
        "ticket", "talep", "yardim", "yardım", "cozum", "çözüm",
    ],
    "hr": [
        "izin", "maas", "maaş", "personel", "calisan", "çalışan",
        "ise alim", "işe alım", "performans", "vardiya", "ozluk", "özlük",
    ],
    "engineering": [
        "kod", "code", "bug", "deploy", "git", "api", "server",
        "migration", "test", "build", "release",
    ],
    "operations": [
        "operasyon", "süreç", "surec", "prosedür", "prosedur",
        "envanter", "stok", "lojistik", "teslimat", "kargo",
    ],
    "legal": [
        "hukuk", "dava", "mahkeme", "avukat", "kanun", "mevzuat",
        "tck", "cmk", "savunma", "iddianame", "beraat",
    ],
    "desktop": [
        "uygulama", "aç", "ac", "kapat", "dosya", "klasör", "klasor",
        "desktop", "indir", "indirme", "chrome", "safari", "telegram",
        "whatsapp", "finder", "terminal", "word", "excel",
        "ses aç", "sesi aç", "ses kapat", "sesi kapat", "sesi kıs", "sesi kis",
        "ses arti", "ses artır", "ses artir", "ses azalt", "sesi azalt",
        "ses yükselt", "ses yukselt", "ses seviyesi", "sessize al", "mute",
        "volume", "parlaklık", "parlaklik", "ekran görüntüsü",
        "screenshot", "url", "tarayıcı", "tarayici", "browser",
        "ekranda ne var", "ne görüyorsun", "ekranı analiz", "ekranı kontrol",
        "belge oluştur", "belge olustur", "doküman", "dokuman", "docx", "xlsx",
        "spotify", "müzik", "muzik", "uygulama aç", "uygulama kapat",
    ],
}

_COMPLEXITY_SIGNALS = {
    "complex": [
        "analiz", "karsilastir", "karşılaştır", "rapor", "detayli", "detaylı",
        "strateji", "plan", "neden", "nasil", "nasıl", "arasindaki",
        "arasındaki", "ilişki", "iliski", "trend", "tahmin",
    ],
    "simple": [
        "ne", "kim", "kac", "kaç", "nerede", "hangi", "listele",
        "göster", "goster", "aç", "ac", "kapat", "merhaba", "selam",
    ],
}


def _turkish_lower(text: str) -> str:
    """Turkish-aware lowercase. Python's .lower() maps İ→i̇ (wrong), we need İ→i, I→ı."""
    result = text.replace("İ", "i").replace("I", "ı")
    return result.lower()


def classify_fast(text: str) -> RoutingDecision:
    """
    Fast keyword-based classification. No LLM call.
    Used for obvious cases to save tokens.
    """
    text_lower = _turkish_lower(text)

    # Domain detection
    domain_scores = {}
    for domain, keywords in _DOMAIN_SIGNALS.items():
        score = sum(1 for kw in keywords if _keyword_match(kw, text_lower))
        if score > 0:
            domain_scores[domain] = score

    if domain_scores:
        domain = max(domain_scores, key=domain_scores.get)
        confidence = min(domain_scores[domain] / 3.0, 1.0)
    else:
        domain = "general"
        confidence = 0.5

    # Complexity detection (word-boundary matching to avoid "ne" in "neden" etc.)
    is_complex = any(_keyword_match(kw, text_lower) for kw in _COMPLEXITY_SIGNALS["complex"])
    is_simple = any(_keyword_match(kw, text_lower) for kw in _COMPLEXITY_SIGNALS["simple"])

    if is_complex and not is_simple:
        complexity = "complex"
    elif is_simple and not is_complex:
        complexity = "simple"
    else:
        complexity = "moderate"

    # Model tier — Sonnet 4 for everything except trivial chat
    from seriai.cognition.constants import TOOL_DOMAINS as _TOOL_DOMAINS
    if domain in _TOOL_DOMAINS:
        model_tier = "standard"  # Sonnet 4 — tool/domain gerektiriyor
    elif complexity == "simple" and confidence > 0.7 and domain == "general":
        model_tier = "fast"      # Haiku 4.5 — basit sohbet
    else:
        model_tier = "standard"  # Sonnet 4 — default

    # Intent
    action_words = ["yap", "gonder", "gönder", "olustur", "oluştur", "ekle", "sil", "guncelle", "güncelle"]
    query_words = ["ne", "kim", "nerede", "hangi", "listele", "göster", "bul"]
    analysis_words = ["analiz", "karsilastir", "rapor", "incele"]

    if any(_keyword_match(w, text_lower) for w in action_words):
        intent = "action"
    elif any(_keyword_match(w, text_lower) for w in analysis_words):
        intent = "analysis"
    elif any(_keyword_match(w, text_lower) for w in query_words):
        intent = "query"
    else:
        intent = "chat"

    # Suggested tools based on domain
    suggested_tools = _get_domain_tools(domain)
    context_needs = _get_context_needs(domain)

    return RoutingDecision(
        domain=domain,
        intent=intent,
        complexity=complexity,
        suggested_tools=suggested_tools,
        model_tier=model_tier,
        context_needs=context_needs,
        confidence=confidence,
    )


def _get_domain_tools(domain: str) -> list:
    """Return tool names relevant to a domain."""
    _DOMAIN_TOOLS = {
        "general": ["web_search", "create_word_document", "create_excel_document", "search_telegram", "list_telegram_chats", "get_chat_messages"],
        "crm": ["db_query", "db_schema", "db_describe_table", "web_search", "create_word_document", "create_excel_document", "search_telegram", "list_telegram_chats", "get_chat_messages"],
        "support": ["db_query", "db_schema", "web_search"],
        "hr": ["db_query", "db_schema"],
        "engineering": ["web_search"],
        "operations": ["db_query", "db_schema", "create_excel_document"],
        "legal": ["web_search"],
        "desktop": ["open_app", "close_app", "open_file", "list_files", "create_file", "computer_settings", "open_url", "create_word_document", "create_excel_document", "screen_check", "analyze_image"],
    }
    return _DOMAIN_TOOLS.get(domain, [])


def _get_context_needs(domain: str) -> list:
    """Return what context to inject for a domain."""
    _CONTEXT_MAP = {
        "general": ["org_knowledge"],
        "crm": ["db_schema", "org_knowledge", "process_knowledge"],
        "support": ["db_schema", "support_patterns"],
        "hr": ["db_schema"],
        "engineering": ["engineering_notes"],
        "operations": ["db_schema", "process_knowledge"],
        "legal": [],
        "desktop": [],
    }
    return _CONTEXT_MAP.get(domain, [])
