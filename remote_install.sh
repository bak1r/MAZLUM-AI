#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  MAZLUM Tek Satır Kurulum — Cahil dostu
#  Kullanım: curl -sL https://raw.githubusercontent.com/bak1r/MAZLUM-AI/main/remote_install.sh | bash
# ═══════════════════════════════════════════════════════════════

set -e

GREEN='\033[92m'
RED='\033[91m'
YELLOW='\033[93m'
CYAN='\033[96m'
BOLD='\033[1m'
RESET='\033[0m'

say() { echo -e "${GREEN}✅ $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠️  $1${RESET}"; }
fail() { echo -e "${RED}❌ $1${RESET}"; exit 1; }
step() { echo -e "\n${CYAN}${BOLD}── $1 ──${RESET}\n"; }

echo ""
echo -e "${CYAN}${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${CYAN}${BOLD}       MAZLUM OTOMATİK KURULUM         ${RESET}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════${RESET}"
echo ""
echo -e "${YELLOW}Hiçbir şey yapmanıza gerek yok.${RESET}"
echo -e "${YELLOW}Kurulum otomatik ilerleyecek.${RESET}"
echo -e "${YELLOW}Sadece şifre sorulursa Mac şifrenizi yazın.${RESET}"
echo ""

# ── 1. macOS kontrolü ─────────────────────────────────────────
step "1/7 — Sistem kontrol ediliyor"
if [[ "$(uname)" != "Darwin" ]]; then
    fail "Bu script sadece macOS için. Windows desteklenmiyor."
fi
say "macOS tespit edildi: $(sw_vers -productVersion)"

# ── 2. Xcode Command Line Tools ──────────────────────────────
step "2/7 — Geliştirici araçları kontrol ediliyor"
if ! xcode-select -p &>/dev/null; then
    warn "Xcode Command Line Tools kuruluyor... (biraz sürebilir)"
    xcode-select --install 2>/dev/null || true
    echo ""
    echo -e "${YELLOW}Ekranda bir pencere açılacak.${RESET}"
    echo -e "${YELLOW}'Install' butonuna basın ve bitmesini bekleyin.${RESET}"
    echo -e "${YELLOW}Bittikten sonra Enter'a basın.${RESET}"
    read -r -p "Kurulum bitti mi? Enter'a basın: "

    if ! xcode-select -p &>/dev/null; then
        fail "Xcode araçları kurulamadı. Tekrar deneyin."
    fi
fi
say "Xcode Command Line Tools hazır"

# ── 3. Homebrew ───────────────────────────────────────────────
step "3/7 — Homebrew kontrol ediliyor"
if ! command -v brew &>/dev/null; then
    warn "Homebrew kuruluyor..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # M1/M2 Mac'lerde Homebrew PATH'e eklenmeli
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    fi
fi
say "Homebrew hazır: $(brew --version | head -1)"

# ── 4. Python 3.11+ ──────────────────────────────────────────
step "4/7 — Python kontrol ediliyor"
NEED_PYTHON=false

if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
    PY_MINOR=$(echo $PY_VER | cut -d. -f2)
    if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MINOR" -lt 10 ]]; then
        warn "Python $PY_VER çok eski, 3.11 kuruluyor..."
        NEED_PYTHON=true
    else
        say "Python $PY_VER yeterli"
    fi
else
    NEED_PYTHON=true
fi

if [[ "$NEED_PYTHON" == "true" ]]; then
    brew install python@3.11
    say "Python 3.11 kuruldu"
fi

# ── 5. Git + Repo ────────────────────────────────────────────
step "5/7 — MAZLUM indiriliyor"
INSTALL_DIR="$HOME/MAZLUM-AI"

if [[ -d "$INSTALL_DIR" ]]; then
    warn "MAZLUM zaten mevcut, güncelleniyor..."
    cd "$INSTALL_DIR"
    git pull origin main 2>/dev/null || true
else
    git clone https://github.com/bak1r/MAZLUM-AI.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
say "MAZLUM indirildi: $INSTALL_DIR"

# ── 6. Bağımlılıklar ─────────────────────────────────────────
step "6/7 — Bağımlılıklar kuruluyor"

# portaudio (ses için)
if ! brew list portaudio &>/dev/null; then
    brew install portaudio
fi

# pip bağımlılıkları
python3 -m pip install --upgrade pip --quiet
python3 -m pip install -r requirements.txt --quiet 2>/dev/null || {
    warn "Bazı paketler sorun çıkardı, tekrar deneniyor..."
    python3 -m pip install -r requirements.txt
}
say "Tüm bağımlılıklar kuruldu"

# ── 7. Setup Wizard ──────────────────────────────────────────
step "7/8 — Ayarlar yapılandırılıyor"

if [[ -f ".env" ]]; then
    warn ".env zaten var, setup wizard atlanıyor"
    warn "Yeniden ayarlamak için: cd $INSTALL_DIR && python3 setup_wizard.py"
else
    python3 setup_wizard.py
fi

# ── 8. macOS İzinleri ─────────────────────────────────────────
step "8/8 — macOS izinleri veriliyor"

echo -e "${YELLOW}${BOLD}ÖNEMLİ: Şimdi 3 tane izin penceresi açılacak.${RESET}"
echo -e "${YELLOW}Her birinde 'Terminal' uygulamasını bulup yanındaki${RESET}"
echo -e "${YELLOW}anahtarı AÇIK konuma getirin (yeşil olacak).${RESET}"
echo -e "${YELLOW}Eğer Terminal listede yoksa + butonuna basıp ekleyin.${RESET}"
echo ""

# Mikrofon
echo -e "${CYAN}1) MİKROFON izni açılıyor...${RESET}"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone" 2>/dev/null || \
open "/System/Library/PreferencePanes/Security.prefPane" 2>/dev/null
echo -e "${YELLOW}   → Terminal'in yanındaki anahtarı AÇ yapın${RESET}"
read -r -p "   Açtınız mı? Enter'a basın: "

# Ekran Kaydı
echo -e "${CYAN}2) EKRAN KAYDI izni açılıyor...${RESET}"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" 2>/dev/null
echo -e "${YELLOW}   → Terminal'in yanındaki anahtarı AÇ yapın${RESET}"
read -r -p "   Açtınız mı? Enter'a basın: "

# Erişilebilirlik
echo -e "${CYAN}3) ERİŞİLEBİLİRLİK izni açılıyor...${RESET}"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null
echo -e "${YELLOW}   → Terminal'in yanındaki anahtarı AÇ yapın${RESET}"
read -r -p "   Açtınız mı? Enter'a basın: "

# Tam Disk Erişimi
echo -e "${CYAN}4) TAM DİSK ERİŞİMİ izni açılıyor...${RESET}"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null
echo -e "${YELLOW}   → Terminal'in yanındaki anahtarı AÇ yapın${RESET}"
read -r -p "   Açtınız mı? Enter'a basın: "

say "Tüm izinler ayarlandı"

# ── Bitti ─────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${CYAN}${BOLD}       KURULUM TAMAMLANDI! 🎉          ${RESET}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════${RESET}"
echo ""
echo -e "${GREEN}MAZLUM'u başlatmak için:${RESET}"
echo -e "${BOLD}  cd $INSTALL_DIR && python3 main.py${RESET}"
echo ""
echo -e "${GREEN}Veya Finder'dan çift tıkla:${RESET}"
echo -e "${BOLD}  $INSTALL_DIR/MAZLUM_BASLAT.command${RESET}"
echo ""
