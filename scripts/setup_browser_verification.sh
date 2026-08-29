#!/usr/bin/env bash
# Playwright 브라우저 검증 환경을 sudo 없이 준비한다.
#
# WSL 기본 이미지에는 Chromium 이 필요로 하는 libnss3·libnspr4·libasound2 가 없고
# 한글 폰트도 없다. 시스템에 설치하려면 sudo 가 필요하므로, deb 를 내려받아 홈
# 아래에 풀어 LD_LIBRARY_PATH 로 붙인다. 시스템은 건드리지 않는다.
#
#   bash scripts/setup_browser_verification.sh
#   source .cache/browser-verify/env.sh
#   uv run python scripts/verify_chat_ui.py --help
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$ROOT/.cache/browser-verify"
mkdir -p "$CACHE/debs" "$CACHE/root"

echo "[1/4] Playwright 와 Chromium"
uv run playwright install chromium >/dev/null

echo "[2/4] 공유 라이브러리 (sudo 없이)"
cd "$CACHE/debs"
# 패키지 이름은 배포판에 따라 다르다(noble 은 libasound2t64).
apt-get download libnss3 libnspr4 libasound2t64 2>/dev/null \
  || apt-get download libnss3 libnspr4 libasound2
for deb in *.deb; do dpkg-deb -x "$deb" "$CACHE/root"; done

echo "[3/4] 한글 폰트"
apt-get download fonts-nanum 2>/dev/null || true
for deb in fonts-nanum*.deb; do [ -e "$deb" ] && dpkg-deb -x "$deb" "$CACHE/root"; done
mkdir -p "$HOME/.local/share/fonts"
find "$CACHE/root" -name '*.ttf' -exec cp -n {} "$HOME/.local/share/fonts/" \; 2>/dev/null || true
fc-cache -f >/dev/null 2>&1 || true

echo "[4/4] 환경 파일"
LIBDIR="$(find "$CACHE/root" -name 'libnss3.so' -printf '%h\n' | head -1)"
cat > "$CACHE/env.sh" <<ENVEOF
# source 해서 쓴다. Playwright 가 Chromium 을 띄울 때 필요하다.
export LD_LIBRARY_PATH="$LIBDIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
ENVEOF

echo
echo "준비 완료. 다음을 실행한다:"
echo "  source .cache/browser-verify/env.sh"
echo "  uv run python scripts/verify_chat_ui.py \"질문\" --tag n3 --out out/"
