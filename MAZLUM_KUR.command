#!/bin/bash
# ═══════════════════════════════════════════════════
#  MAZLUM Kurulum — Çift tıkla, gerisini bırak
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

# Python kontrolü
if ! command -v python3 &> /dev/null; then
    echo "  ❌ Python3 bulunamadı!"
    echo ""
    echo "  Önce Python kur:"
    echo "    https://www.python.org/downloads/"
    echo ""
    echo "  veya terminalde:"
    echo "    brew install python3"
    echo ""
    read -p "  Çıkmak için Enter'a bas..."
    exit 1
fi

PYVER=$(python3 --version 2>&1)
echo "  ✅ $PYVER bulundu"

# Python 3.9+ kontrolü
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
if [ "$PY_MINOR" -lt 9 ] 2>/dev/null; then
    echo ""
    echo "  ❌ Python 3.9 veya üstü gerekli!"
    echo "  Mevcut: $PYVER"
    echo ""
    echo "  Güncelle: https://www.python.org/downloads/"
    echo ""
    read -p "  Çıkmak için Enter'a bas..."
    exit 1
fi
echo ""

# pip kontrolü
if ! python3 -m pip --version &> /dev/null; then
    echo "  ❌ pip bulunamadı, kuruluyor..."
    python3 -m ensurepip --upgrade 2>/dev/null
fi

# Wizard başlat
python3 setup_wizard.py

echo ""
read -p "  Çıkmak için Enter'a bas..."
