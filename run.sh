#!/usr/bin/env bash
# تشغيل "بنكي".
#   ./run.sh         ← على الماك بس (http://localhost:8000)
#   ./run.sh phone   ← كمان من الآيفون (عن طريق Tailscale أو نفس الـ Wi-Fi)
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "بدك Python 3.10 أو أحدث. نزّله من https://www.python.org/downloads/macos/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "أول تشغيل: بنزّل المكتبات…"
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

HOST=127.0.0.1
if [ "${1:-}" = "phone" ]; then
  HOST=0.0.0.0
  echo "افتح من الآيفون:"
  if command -v tailscale >/dev/null 2>&1; then
    echo "  http://$(tailscale ip -4 2>/dev/null | head -1):8000   (Tailscale — الأفضل)"
  fi
  IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)
  if [ -n "$IP" ]; then
    echo "  http://$IP:8000   (نفس الـ Wi-Fi بس — شبكة البيت، مش شبكة عامة)"
  fi
fi

echo "بنكي شغّال على http://localhost:8000 — للإيقاف: Ctrl+C"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port 8000
