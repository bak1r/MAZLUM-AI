#!/bin/bash
# ═══════════════════════════════════════════════════
#  MAZLUM Başlat — Çift tıkla, çalışsın
# ═══════════════════════════════════════════════════

cd "$(dirname "$0")"

clear
echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║                                           ║"
echo "  ║         MAZLUM BAŞLATILIYOR...             ║"
echo "  ║                                           ║"
echo "  ╚═══════════════════════════════════════════╝"
echo ""

# Python kontrolü
if ! command -v python3 &> /dev/null; then
    echo "  ❌ Python3 bulunamadı! Önce MAZLUM_KUR'u çalıştır."
    read -p "  Çıkmak için Enter'a bas..."
    exit 1
fi

# .env kontrolü
if [ ! -f ".env" ]; then
    echo "  ❌ .env dosyası yok! Önce kurulum yapmalısın."
    echo ""
    echo "  MAZLUM_KUR.command dosyasına çift tıkla."
    echo ""
    read -p "  Çıkmak için Enter'a bas..."
    exit 1
fi

# Sanal ortam kontrolü (varsa aktifle)
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "  ✅ Sanal ortam aktif"
elif [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "  ✅ Sanal ortam aktif"
fi

echo "  🚀 MAZLUM başlıyor..."
echo "  (Kapatmak için Ctrl+C)"
echo ""
echo "  ─────────────────────────────────────────────"
echo ""

python3 main.py

echo ""
echo "  MAZLUM kapatıldı."
read -p "  Çıkmak için Enter'a bas..."
