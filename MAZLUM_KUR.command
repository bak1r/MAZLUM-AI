#!/bin/bash
# ═══════════════════════════════════════════════════
#  MAZLUM Kurulum — Çift tıkla, gerisini bırak
#  Her şeyi sıfırdan kurar: Homebrew, Python, pip, paketler
# ═══════════════════════════════════════════════════

cd "$(dirname "$0")"

clear
echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║                                           ║"
echo "  ║         MAZLUM KURULUM BAŞLIYOR            ║"
echo "  ║                                           ║"
echo "  ╚═══════════════════════════════════════════╝"
echo ""

# ── 1. Xcode Command Line Tools (macOS temel gereksinim) ──
if ! xcode-select -p &> /dev/null; then
    echo "  ⏳ Xcode Command Line Tools kuruluyor..."
    echo "  (macOS pencere açabilir — Kur/Install'a bas)"
    echo ""
    xcode-select --install 2>/dev/null
    # Kullanıcı GUI'den onaylayana kadar bekle
    echo "  Kurulum penceresi açıldıysa 'Kur' butonuna bas."
    echo "  Bittikten sonra Enter'a bas..."
    read -p "  "
    echo ""
fi
echo "  ✅ Xcode CLI Tools kurulu"

# ── 2. Homebrew ──
if ! command -v brew &> /dev/null; then
    echo "  ⏳ Homebrew kuruluyor (macOS paket yöneticisi)..."
    echo "  (Şifren istenebilir — bilgisayar şifren)"
    echo ""
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Apple Silicon (M1/M2/M3) path ayarı
    if [ -f "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
        # Kalıcı olsun
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile 2>/dev/null
    fi
    echo ""
fi

if command -v brew &> /dev/null; then
    echo "  ✅ Homebrew kurulu"
else
    echo "  ⚠️ Homebrew kurulamadı — ses özelliği çalışmayabilir"
    echo "  Devam ediyorum..."
fi

# ── 3. Python 3 ──
if ! command -v python3 &> /dev/null; then
    echo "  ⏳ Python 3 kuruluyor..."
    if command -v brew &> /dev/null; then
        brew install python3
    else
        echo ""
        echo "  ❌ Python3 bulunamadı ve Homebrew yok!"
        echo ""
        echo "  Elle kur: https://www.python.org/downloads/"
        echo ""
        read -p "  Kurduktan sonra bu dosyaya tekrar çift tıkla. Enter'a bas..."
        exit 1
    fi
fi

PYVER=$(python3 --version 2>&1)
echo "  ✅ $PYVER bulundu"

# Python 3.9+ kontrolü
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
if [ "$PY_MINOR" -lt 9 ] 2>/dev/null; then
    echo ""
    echo "  ⏳ Python güncelleniyor (3.9+ gerekli)..."
    if command -v brew &> /dev/null; then
        brew install python@3.12
        # Yeni python'u kullan
        export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"
        export PATH="/usr/local/opt/python@3.12/bin:$PATH"
    else
        echo "  ❌ Python $PYVER çok eski! 3.9+ gerekli."
        echo "  Güncelle: https://www.python.org/downloads/"
        echo ""
        read -p "  Çıkmak için Enter'a bas..."
        exit 1
    fi
    PYVER=$(python3 --version 2>&1)
    echo "  ✅ $PYVER"
fi
echo ""

# ── 4. pip ──
if ! python3 -m pip --version &> /dev/null; then
    echo "  ⏳ pip kuruluyor..."
    python3 -m ensurepip --upgrade 2>/dev/null || curl -sS https://bootstrap.pypa.io/get-pip.py | python3
fi
echo "  ✅ pip kurulu"

# ── 5. PortAudio (ses için) ──
if command -v brew &> /dev/null; then
    if ! brew list portaudio &> /dev/null; then
        echo "  ⏳ PortAudio kuruluyor (ses motoru için)..."
        brew install portaudio 2>/dev/null
    fi
    echo "  ✅ PortAudio kurulu"
fi

echo ""
echo "  ────────────────────────────────────────────"
echo "  Temel araçlar tamam. Wizard başlıyor..."
echo "  ────────────────────────────────────────────"
echo ""

# Wizard başlat
python3 setup_wizard.py

echo ""
read -p "  Çıkmak için Enter'a bas..."
