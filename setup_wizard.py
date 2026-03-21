#!/usr/bin/env python3
"""
MAZLUM Setup Wizard — Yeni bilgisayara kurulum.
Tek komutla her seyi ayarlar: python3 setup_wizard.py
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"
MEMORY_DIR = DATA_DIR / "memory"

# ── Terminal renkleri ──────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def cprint(msg, color=RESET):
    print(f"{color}{msg}{RESET}")


def ask(prompt, default="", secret=False, required=True):
    """Kullanicidan input al."""
    suffix = f" [{default}]" if default else ""
    while True:
        if secret:
            import getpass
            val = getpass.getpass(f"  {prompt}{suffix}: ").strip()
        else:
            val = input(f"  {prompt}{suffix}: ").strip()
        if val:
            return val
        if default:
            return default
        if not required:
            return ""
        cprint("  Bu alan zorunlu!", RED)


def ask_port(prompt, default="8420"):
    """Port numarasi al ve dogrula."""
    while True:
        val = ask(prompt, default=default, required=True)
        try:
            port = int(val)
            if 1 <= port <= 65535:
                return str(port)
            cprint("  Port 1-65535 arasinda olmali!", RED)
        except ValueError:
            cprint("  Gecerli bir sayi girin!", RED)


def ask_yn(prompt, default=True):
    """Evet/hayir sorusu."""
    suffix = "[E/h]" if default else "[e/H]"
    val = input(f"  {prompt} {suffix}: ").strip().lower()
    if not val:
        return default
    return val in ("e", "evet", "y", "yes", "1")


def banner():
    print()
    cprint("=" * 55, CYAN)
    cprint("       MAZLUM KURULUM SIHIRBAZI", BOLD)
    cprint("=" * 55, CYAN)
    print()
    cprint("  Bu sihirbaz MAZLUM'u sifirdan ayarlar.", YELLOW)
    cprint("  Her adimda ne girecegini sana soyluyorum.", YELLOW)
    cprint("  Bos birakip Enter'a basarsan varsayilan deger kullanilir.", YELLOW)
    print()


def step_dependencies():
    """Adim 1: Python bagimliliklari kur."""
    cprint("[1/7] BAGIMLILIKLAR", BOLD)

    # Python version kontrolu (3.9+ zorunlu)
    if sys.version_info < (3, 9):
        cprint(f"  ❌ Python {sys.version_info.major}.{sys.version_info.minor} tespit edildi.", RED)
        cprint("  MAZLUM icin Python 3.9 veya ustu gerekli!", RED)
        cprint("  https://www.python.org/downloads/ adresinden guncelle.", YELLOW)
        return False

    cprint(f"  Python {sys.version_info.major}.{sys.version_info.minor}: uygun ✓", GREEN)
    cprint("  Python paketleri kuruluyor...", YELLOW)

    req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        cprint("  requirements.txt bulunamadi!", RED)
        return False

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        cprint("  Paketler kuruldu.", GREEN)
    except subprocess.CalledProcessError as e:
        cprint(f"  Paket kurulumu basarisiz: {e}", RED)
        cprint("  Elle deneyin: pip install -r requirements.txt", YELLOW)
        return False

    # PyAudio — opsiyonel
    try:
        import pyaudio  # noqa
        cprint("  PyAudio: zaten kurulu.", GREEN)
    except ImportError:
        if ask_yn("  Ses ozelligi (mikrofon) kullanacak misin?", default=False):
            cprint("  PyAudio kuruluyor (brew + pip)...", YELLOW)
            try:
                subprocess.check_call(["brew", "install", "portaudio"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.check_call([sys.executable, "-m", "pip", "install", "pyaudio", "-q"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                cprint("  PyAudio kuruldu.", GREEN)
            except Exception:
                cprint("  PyAudio kurulamadi. Ses ozelligi devre disi kalacak.", YELLOW)
                cprint("  Elle: brew install portaudio && pip install pyaudio", YELLOW)

    # DB driver
    print()
    return True


def step_api_keys():
    """Adim 2: API key'leri al."""
    cprint("[2/7] API ANAHTARLARI", BOLD)
    print()

    config = {}

    cprint("  Anthropic (Claude) — ANA BEYIN", CYAN)
    cprint("  https://console.anthropic.com/settings/keys adresinden al.", YELLOW)
    config["ANTHROPIC_API_KEY"] = ask("Anthropic API Key", secret=True)

    print()
    cprint("  Google (Gemini) — SES MOTORU (opsiyonel)", CYAN)
    cprint("  https://aistudio.google.com/apikey adresinden al.", YELLOW)
    cprint("  Ses ozelligi kullanmayacaksan bos birak.", YELLOW)
    config["GOOGLE_API_KEY"] = ask("Google API Key", secret=True, required=False)

    return config


def step_telegram():
    """Adim 3: Telegram ayarlari."""
    cprint("[3/7] TELEGRAM", BOLD)
    print()

    config = {}

    if not ask_yn("Telegram Bot kullanacak misin?", default=True):
        return config

    # ── Bot token rehberi ──
    print()
    cprint("  ┌─────────────────────────────────────────────────┐", CYAN)
    cprint("  │  Mevcut bir bot token'in varsa onu kullanabilir- │", CYAN)
    cprint("  │  sin. Ayni token'i ayni anda iki bilgisayarda   │", CYAN)
    cprint("  │  calistiramazsin (Telegram kisitlamasi).         │", CYAN)
    cprint("  │  Yoksa yeni bot olusturman gerekir.             │", CYAN)
    cprint("  └─────────────────────────────────────────────────┘", CYAN)
    print()

    if ask_yn("Yeni bot olusturmak icin rehber ister misin?", default=False):
        cprint("  Simdi BotFather'i aciyorum...", YELLOW)
        print()

        # BotFather'i ac (platform bagimsiz)
        import webbrowser
        try:
            webbrowser.open("https://t.me/BotFather")
        except Exception:
            cprint("  (Tarayici acilamadi — elle git: https://t.me/BotFather)", RED)

        cprint("  ┌─── BOT OLUSTURMA ADIMLARI ───────────────────┐", GREEN)
        cprint("  │                                               │", GREEN)
        cprint("  │  1. BotFather'a /newbot yaz                   │", GREEN)
        cprint("  │  2. Bot'a isim ver:  MAZLUM - <CihazAdi>      │", GREEN)
        cprint("  │     ornek: 'MAZLUM - MacBook Pro'              │", GREEN)
        cprint("  │  3. Username ver:  mazlum_<cihaz>_bot          │", GREEN)
        cprint("  │     ornek: 'mazlum_macbookpro_bot'             │", GREEN)
        cprint("  │  4. BotFather sana token verecek — kopyala     │", GREEN)
        cprint("  │                                               │", GREEN)
        cprint("  └───────────────────────────────────────────────┘", GREEN)
        print()
        input("  Bot'u olusturduysan Enter'a bas...")
        print()

    config["SERIAI_TELEGRAM_BOT_TOKEN"] = ask("Bot Token", secret=True)

    cprint("  Hangi Telegram hesaplari botu kullanabilsin?", YELLOW)
    cprint("  @userinfobot'a /start yaz, ID'ni ogren.", YELLOW)
    while True:
        raw_ids = ask("Telegram User ID (virgullu)")
        if raw_ids:
            parts = [p.strip() for p in raw_ids.split(",")]
            if all(p.lstrip("-").isdigit() for p in parts if p):
                config["SERIAI_TELEGRAM_ALLOWED_USERS"] = ",".join(parts)
                break
            else:
                cprint("  HATA: User ID sadece sayi olmali (ornek: 123456789,987654321)", RED)
        else:
            break  # bos birakilabilir

    print()
    if ask_yn("Telegram hesabindan mesaj izleme (Telethon) kullanacak misin?", default=False):
        cprint("  https://my.telegram.org adresinden API bilgilerini al.", YELLOW)
        cprint("  (API ID ve Hash tum cihazlarda AYNI olabilir — sorun yok.)", YELLOW)
        config["SERIAI_TG_API_ID"] = ask("Telegram API ID")
        config["SERIAI_TG_API_HASH"] = ask("Telegram API Hash")
        config["SERIAI_TG_PHONE"] = ask("Telefon numarasi (+905xx...)")

    return config


def step_database():
    """Adim 4: Veritabani ayarlari."""
    cprint("[4/7] VERITABANI (opsiyonel)", BOLD)
    print()

    config = {}

    if not ask_yn("Veritabani baglantisi kullanacak misin?", default=False):
        return config

    cprint("  Sadece READ-ONLY erisim. Veri degistirme yok.", YELLOW)
    print()

    engine = ask("Veritabani turu (postgresql/mysql/mssql)", default="postgresql")
    config["SERIAI_DB_ENGINE"] = engine

    config["SERIAI_DB_HOST"] = ask("Host", default="localhost")

    default_port = {"postgresql": "5432", "mysql": "3306", "mssql": "1433"}.get(engine, "5432")
    config["SERIAI_DB_PORT"] = ask_port("Port", default=default_port)

    config["SERIAI_DB_NAME"] = ask("Veritabani adi")
    config["SERIAI_DB_USER"] = ask("Kullanici adi")
    config["SERIAI_DB_PASSWORD"] = ask("Sifre", secret=True)

    # DB driver kurulumu
    driver_map = {
        "postgresql": "psycopg2-binary",
        "mysql": "pymysql",
        "mssql": "pyodbc",
    }
    driver = driver_map.get(engine)
    if driver:
        cprint(f"  {driver} kuruluyor...", YELLOW)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", driver, "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            cprint(f"  {driver} kuruldu.", GREEN)
        except Exception:
            cprint(f"  {driver} kurulamadi. Elle: pip install {driver}", RED)

    return config


def step_features():
    """Adim 5: Ozellik secimi."""
    cprint("[5/7] OZELLIKLER", BOLD)
    print()

    config = {}

    config["SERIAI_ENABLE_WEB_UI"] = "true" if ask_yn("Web arayuzu (tarayicida kullan)?", default=True) else "false"

    has_voice = False
    try:
        import pyaudio  # noqa
        has_voice = True
    except ImportError:
        pass

    if has_voice:
        config["SERIAI_ENABLE_VOICE"] = "true" if ask_yn("Ses modu (mikrofon + hoparlor)?", default=True) else "false"
    else:
        config["SERIAI_ENABLE_VOICE"] = "false"

    port = ask_port("Web UI portu", default="8420")
    config["SERIAI_WEB_PORT"] = port

    config["SERIAI_MODE"] = "all"

    return config


def step_personalize():
    """Adim 6: Kisisel bilgiler + hafiza aktarimi."""
    cprint("[6/7] KISISELLISTIRME", BOLD)
    print()

    config = {}

    name = ask("Senin ismin (MAZLUM sana boyle hitap edecek)", default="Efendim")
    config["SERIAI_OWNER_NAME"] = name

    lang = ask("Dil (tr/en)", default="tr")
    config["SERIAI_LANGUAGE"] = lang

    # ── Hafiza Aktarimi ──
    print()
    cprint("  ┌─── MAZLUM HAFIZASI ────────────────────────────┐", CYAN)
    cprint("  │                                                │", CYAN)
    cprint("  │  MAZLUM konusmalardan ogrenir ve hatirlar:     │", CYAN)
    cprint("  │  - Kisisel bilgiler (isimler, roller)          │", CYAN)
    cprint("  │  - Is surecleri ve kurallar                    │", CYAN)
    cprint("  │  - Musteri bilgileri                           │", CYAN)
    cprint("  │  - Hatalardan ogrendikleri                     │", CYAN)
    cprint("  │                                                │", CYAN)
    cprint("  │  Eski bilgisayardan hafizayi aktarabilirsin!   │", CYAN)
    cprint("  └────────────────────────────────────────────────┘", CYAN)
    print()

    if ask_yn("Eski bilgisayardan hafiza aktarmak ister misin?", default=False):
        cprint("  Eski bilgisayarda su komutu calistir:", YELLOW)
        cprint("    cd MAZLUM-AI && python3 -c \"", YELLOW)
        cprint("    from seriai.memory.manager import MemoryManager", YELLOW)
        cprint("    from pathlib import Path", YELLOW)
        cprint("    m = MemoryManager(Path('data/memory'))", YELLOW)
        cprint("    m.export_memory(Path('mazlum_hafiza.json'))", YELLOW)
        cprint("    print('Dosya olusturuldu: mazlum_hafiza.json')\"", YELLOW)
        print()
        cprint("  Sonra o dosyayi bu bilgisayara kopyala.", YELLOW)
        print()

        import_path = ask("Hafiza dosyasinin yolu (mazlum_hafiza.json)", required=False)
        if import_path:
            p = Path(import_path).expanduser()
            if p.exists():
                try:
                    # Import islemi
                    sys.path.insert(0, str(BASE_DIR))
                    from seriai.memory.manager import MemoryManager
                    mem = MemoryManager(MEMORY_DIR)
                    count = mem.import_memory(p, merge=True)
                    cprint(f"  ✅ {count} hafiza kaydi aktarildi!", GREEN)
                except Exception as e:
                    cprint(f"  ❌ Hafiza aktarimi basarisiz: {e}", RED)
            else:
                cprint(f"  ❌ Dosya bulunamadi: {p}", RED)
                cprint("  Sorun degil — MAZLUM sifirdan da ogrenebilir.", YELLOW)
    else:
        cprint("  Tamam — MAZLUM sifirdan ogrenecek. Zamanla seni taniyacak.", YELLOW)

    return config


def step_macos_permissions():
    """Adim 7: macOS izinleri otomatik ayarla."""
    import platform
    if platform.system() != "Darwin":
        return  # Sadece macOS

    cprint("[7/7] macOS IZINLERI", BOLD)
    print()

    # ── Mikrofon izni ──
    cprint("  Mikrofon izni kontrol ediliyor...", YELLOW)
    # Mikrofon izni ilk PyAudio veya main.py calistiginda macOS otomatik sorar
    cprint("  → Ilk calistirmada macOS mikrofon izni isteyecek — IZIN VER.", CYAN)
    print()

    # ── Accessibility izni (AppleScript tuş simülasyonu için) ──
    cprint("  Accessibility izni ayarlaniyor...", YELLOW)
    cprint("  (Telegram'da sohbet acma icin gerekli)", YELLOW)
    print()

    # Accessibility panel'ini ac
    try:
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print()
        cprint("  ┌─────────────────────────────────────────────┐", CYAN)
        cprint("  │  Sistem Tercihleri acildi.                  │", CYAN)
        cprint("  │                                             │", CYAN)
        cprint("  │  1. Sol alttaki '+' butonuna bas            │", CYAN)
        cprint("  │  2. Uygulamalar → Araclar → Terminal sec    │", CYAN)
        cprint("  │  3. Toggle'i AC yap (mavi olsun)            │", CYAN)
        cprint("  │                                             │", CYAN)
        cprint("  │  Bu islemi bir kez yapman yeterli.          │", CYAN)
        cprint("  └─────────────────────────────────────────────┘", CYAN)
        print()
        input("  Izni verdiysen Enter'a bas...")

        # Dogrulama testi
        cprint("  Accessibility testi yapiliyor...", YELLOW)
        test_result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to return name of first process'],
            capture_output=True, text=True, timeout=5
        )
        if test_result.returncode == 0:
            cprint("  Accessibility: CALISIYOR ✓", GREEN)
        else:
            cprint("  Accessibility: HENUZ AKTIF DEGIL", RED)
            cprint("  Sorun yok — sonra da ayarlayabilirsin.", YELLOW)
            cprint("  Ama Telegram sohbet acma ozelligi calismaz.", YELLOW)
    except Exception:
        cprint("  Sistem Tercihleri acilamadi. Elle ayarla:", YELLOW)
        cprint("  Sistem Tercihleri → Gizlilik → Erisilebilirlik → Terminal ekle", YELLOW)

    print()

    # ── Bildirim izni ──
    cprint("  Bildirimler: Ilk calistirmada macOS otomatik soracak.", CYAN)
    print()


def write_env(all_config):
    """Tum ayarlari .env dosyasina yaz."""
    # Mevcut .env varsa yedekle
    if ENV_PATH.exists():
        backup = ENV_PATH.parent / (ENV_PATH.name + ".backup")
        shutil.copy2(ENV_PATH, backup)
        cprint(f"  Mevcut .env yedeklendi: {backup.name}", YELLOW)

    lines = [
        "# ============================================",
        "# MAZLUM Configuration",
        "# Setup Wizard tarafindan olusturuldu",
        "# ============================================",
        "",
    ]

    # Grup basliklari
    groups = {
        "API Anahtarlari": ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY"],
        "Telegram Bot": ["SERIAI_TELEGRAM_BOT_TOKEN", "SERIAI_TELEGRAM_ALLOWED_USERS"],
        "Telegram User API (Telethon)": ["SERIAI_TG_API_ID", "SERIAI_TG_API_HASH", "SERIAI_TG_PHONE"],
        "Veritabani": ["SERIAI_DB_ENGINE", "SERIAI_DB_HOST", "SERIAI_DB_PORT",
                        "SERIAI_DB_NAME", "SERIAI_DB_USER", "SERIAI_DB_PASSWORD"],
        "Ozellikler": ["SERIAI_ENABLE_WEB_UI", "SERIAI_ENABLE_VOICE",
                        "SERIAI_WEB_PORT", "SERIAI_MODE"],
        "Kisisel": ["SERIAI_OWNER_NAME", "SERIAI_LANGUAGE"],
    }

    written_keys = set()
    for group_name, keys in groups.items():
        group_vals = {k: all_config[k] for k in keys if k in all_config and all_config[k]}
        if group_vals:
            lines.append(f"# --- {group_name} ---")
            for k, v in group_vals.items():
                # Always quote — #, $, spaces, quotes hepsi sorun çıkarır
                safe_v = str(v).replace('"', '\\"')
                lines.append(f'{k}="{safe_v}"')
                written_keys.add(k)
            lines.append("")

    # Yazilmayan key'ler
    remaining = {k: v for k, v in all_config.items() if k not in written_keys and v}
    if remaining:
        lines.append("# --- Diger ---")
        for k, v in remaining.items():
            safe_v = str(v).replace('"', '\\"')
            lines.append(f'{k}="{safe_v}"')
        lines.append("")

    # Telemetry — her kurulumda sabit, sahibine hata raporu gönderir
    lines.append("# --- Telemetry (uzaktan hata izleme) ---")
    lines.append('TELEMETRY_BOT_TOKEN="8799564827:AAEIYAEvTl0jvbvI8lZy1BfClQw8t492ZNc"')
    lines.append('TELEMETRY_CHAT_ID="5787979890"')
    lines.append("")

    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")


def create_dirs():
    """Gerekli dizinleri olustur."""
    for d in [DATA_DIR, MEMORY_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def validate(all_config):
    """Ayarlari dogrula."""
    cprint("\nDOGRULAMA", BOLD)
    ok = True

    # Anthropic key
    key = all_config.get("ANTHROPIC_API_KEY", "")
    if key and key.startswith("sk-ant-"):
        cprint("  Anthropic API Key: format dogru", GREEN)
    elif key:
        cprint("  Anthropic API Key: format supheli (sk-ant- ile baslamali)", YELLOW)
    else:
        cprint("  Anthropic API Key: YOK — MAZLUM calismaz!", RED)
        ok = False

    # Telegram bot
    bot = all_config.get("SERIAI_TELEGRAM_BOT_TOKEN", "")
    if bot and ":" in bot:
        cprint("  Telegram Bot Token: format dogru", GREEN)
    elif bot:
        cprint("  Telegram Bot Token: format supheli", YELLOW)

    # DB
    if all_config.get("SERIAI_DB_ENGINE"):
        cprint(f"  Veritabani: {all_config['SERIAI_DB_ENGINE']}://{all_config.get('SERIAI_DB_HOST','')}:{all_config.get('SERIAI_DB_PORT','')}/{all_config.get('SERIAI_DB_NAME','')}", GREEN)

    # Telethon
    if all_config.get("SERIAI_TG_API_ID"):
        cprint("  Telegram Telethon: yapilandirildi", GREEN)

    # Voice
    if all_config.get("SERIAI_ENABLE_VOICE") == "true":
        try:
            import pyaudio  # noqa
            cprint("  Ses modu: PyAudio kurulu, aktif", GREEN)
        except ImportError:
            cprint("  Ses modu: PyAudio YOK, devre disi kalacak", RED)
            ok = False

    return ok


def test_anthropic(all_config):
    """Anthropic API'yi test et."""
    key = all_config.get("ANTHROPIC_API_KEY", "")
    if not key:
        return

    cprint("\n  Anthropic API test ediliyor...", YELLOW)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": "Merhaba, tek kelimeyle cevap ver: calisiyor mu?"}],
        )
        text = resp.content[0].text if resp.content else ""
        cprint(f"  Sonnet 4: CALISIYOR — '{text[:50]}'", GREEN)
    except Exception as e:
        err = str(e)
        if "not_found" in err.lower():
            cprint("  Sonnet 4: Erisim yok. API key kontrol edin.", RED)
        elif "authentication" in err.lower():
            cprint("  API Key HATALI! Kontrol edin.", RED)
        else:
            cprint(f"  API hatasi: {err[:80]}", RED)


def final_summary(all_config):
    """Kurulum ozeti."""
    print()
    cprint("=" * 55, GREEN)
    cprint("       KURULUM TAMAMLANDI!", BOLD)
    cprint("=" * 55, GREEN)
    print()
    cprint("  .env dosyasi olusturuldu.", GREEN)
    cprint("  Dizinler olusturuldu.", GREEN)
    print()
    cprint("  MAZLUM'u baslatmak icin:", CYAN)
    cprint(f"    cd {BASE_DIR}", BOLD)
    cprint("    python3 main.py", BOLD)
    print()

    if all_config.get("SERIAI_ENABLE_WEB_UI") == "true":
        port = all_config.get("SERIAI_WEB_PORT", "8420")
        cprint(f"  Web UI: http://127.0.0.1:{port}", CYAN)

    if all_config.get("SERIAI_TELEGRAM_BOT_TOKEN"):
        cprint("  Telegram: Bot otomatik baslar", CYAN)

    if all_config.get("SERIAI_ENABLE_VOICE") == "true":
        cprint("  Ses: Mikrofon + hoparlor aktif", CYAN)

    if all_config.get("SERIAI_TG_API_ID"):
        cprint("  Telegram izleme: Ilk calistirmada telefona kod gelecek", CYAN)

    print()
    cprint("  Sorun olursa: data/seriai.log dosyasina bak", YELLOW)
    print()


def main():
    banner()

    all_config = {}

    # Her adimda config topluyoruz
    if not step_dependencies():
        cprint("\nBagimlilik kurulumu basarisiz. Devam ediyorum...", YELLOW)

    print()
    all_config.update(step_api_keys())
    print()
    all_config.update(step_telegram())
    print()
    all_config.update(step_database())
    print()
    all_config.update(step_features())
    print()
    all_config.update(step_personalize())
    print()

    # macOS izinleri
    step_macos_permissions()

    # Dizinleri olustur
    create_dirs()

    # .env yaz
    write_env(all_config)

    # Dogrula
    ok = validate(all_config)

    # API test
    if all_config.get("ANTHROPIC_API_KEY"):
        test_anthropic(all_config)

    # Telethon ilk baglanma notu
    if all_config.get("SERIAI_TG_API_ID"):
        print()
        cprint("  NOT: Telethon ilk calistirmada telefonunuza", YELLOW)
        cprint("  dogrulama kodu gonderecek. main.py baslatinca", YELLOW)
        cprint("  terminalde kodu girmeniz gerekecek (tek seferlik).", YELLOW)

    # Ozet
    final_summary(all_config)

    if not ok:
        cprint("  UYARI: Bazi ayarlar eksik/hatali. Yukardaki uyarilara bak.", RED)
        print()


def _notify_owner(msg):
    """Sahibine Telegram bildirimi gönder (wizard sırasında)."""
    import json
    import urllib.request
    TOKEN = "8799564827:AAEIYAEvTl0jvbvI8lZy1BfClQw8t492ZNc"
    CHAT = "5787979890"
    try:
        payload = json.dumps({
            "chat_id": CHAT, "text": msg[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Bildirim gönderilemezse sessizce geç


if __name__ == "__main__":
    try:
        main()
        # Başarılı kurulum bildirimi
        import socket, platform
        hostname = socket.gethostname()
        ip = "?"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        _notify_owner(
            f"🟢 <b>MAZLUM Kurulum Tamamlandı</b>\n"
            f"<b>Cihaz:</b> {hostname}\n"
            f"<b>IP:</b> {ip}\n"
            f"<b>Platform:</b> {platform.platform()}\n"
            f"<b>Python:</b> {platform.python_version()}"
        )
    except KeyboardInterrupt:
        print()
        cprint("\nKurulum iptal edildi. Mevcut ayarlar korundu.", YELLOW)
        _notify_owner(f"🟡 <b>MAZLUM Kurulum İptal Edildi</b> (Ctrl+C)\n<b>Cihaz:</b> {socket.gethostname()}")
        sys.exit(0)
    except Exception as e:
        cprint(f"\n  KURULUM HATASI: {e}", RED)
        import socket, platform, traceback
        tb = traceback.format_exc()
        if len(tb) > 500:
            tb = "..." + tb[-500:]
        _notify_owner(
            f"🔴 <b>MAZLUM Kurulum HATASI</b>\n"
            f"<b>Cihaz:</b> {socket.gethostname()}\n"
            f"<b>Platform:</b> {platform.platform()}\n"
            f"<b>Hata:</b>\n<code>{str(e)[:300]}</code>\n"
            f"<pre>{tb}</pre>"
        )
        sys.exit(1)
