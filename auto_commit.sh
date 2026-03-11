#!/bin/bash

# ============================================
# 🤖 AUTO-COMMIT WATCHER
# Otomatis commit & push setiap ada file berubah
# ============================================

# Warna buat output yang enak dibaca
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- CEK inotify-tools TERINSTALL ---
if ! command -v inotifywait &> /dev/null; then
    echo -e "${RED}❌ inotify-tools belum terinstall.${NC}"
    echo -e "${YELLOW}Jalankan dulu:${NC} sudo apt install inotify-tools"
    exit 1
fi

# --- CEK INI FOLDER GIT ---
if ! git rev-parse --git-dir &> /dev/null; then
    echo -e "${RED}❌ Folder ini bukan git repo.${NC}"
    echo -e "${YELLOW}Jalankan dulu:${NC} git init && git remote add origin <URL_REPO_KAMU>"
    exit 1
fi

echo -e "${GREEN}✅ Auto-commit watcher aktif!${NC}"
echo -e "${BLUE}👀 Memantau perubahan file di:${NC} $(pwd)"
echo -e "${YELLOW}Tekan Ctrl+C untuk stop.${NC}\n"

# --- MULAI NONTON PERUBAHAN ---
inotifywait -m -r -e modify,create,delete,move \
    --exclude '(\.git|node_modules|__pycache__|\.pyc|\.log)' \
    . |
while read path action file; do

    # Tunggu sebentar biar kalau ada banyak perubahan sekaligus, dikumpulin dulu
    sleep 2

    # Cek ada perubahan yang perlu di-commit?
    if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then

        TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
        CHANGED_FILES=$(git status --porcelain | awk '{print $2}' | head -5 | tr '\n' ', ' | sed 's/,$//')

        COMMIT_MSG="auto: update ${CHANGED_FILES} [${TIMESTAMP}]"

        git add -A
        git commit -m "$COMMIT_MSG" --quiet

        # Push ke GitHub
        if git push --quiet 2>/dev/null; then
            echo -e "${GREEN}✅ [${TIMESTAMP}] Pushed:${NC} ${CHANGED_FILES}"
        else
            echo -e "${RED}⚠️  [${TIMESTAMP}] Commit OK tapi push gagal.${NC} Cek koneksi/token GitHub kamu."
        fi
    fi

done
