#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/azeroth-moments"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/azeroth-moments"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/azeroth-moments"
WATCH_DIR="${HOME}/Pictures/Azeroth Moments"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "Installing Azeroth Moments into ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}" "${STATE_DIR}" "${WATCH_DIR}" "${UNIT_DIR}"

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg found: $(ffmpeg -version | head -n1)"
else
  echo "WARNING: ffmpeg/ffprobe not found."
  echo "  Video support requires FFmpeg. On Nobara/Fedora:"
  echo "    sudo dnf install ffmpeg"
  echo "  Image + Discord publishing will still work; Instagram video prep will fail until FFmpeg is installed."
fi

python3 -m venv "${INSTALL_DIR}/venv"
# shellcheck disable=SC1091
source "${INSTALL_DIR}/venv/bin/activate"
pip install --upgrade pip
pip install -e "${ROOT}[dev]"

if [[ ! -f "${CONFIG_DIR}/.env" ]]; then
  cp "${ROOT}/.env.example" "${CONFIG_DIR}/.env"
  chmod 600 "${CONFIG_DIR}/.env"
  echo "Created ${CONFIG_DIR}/.env — edit secrets before enabling Instagram/Discord."
else
  chmod 600 "${CONFIG_DIR}/.env" || true
  echo "Keeping existing ${CONFIG_DIR}/.env"
  # Merge any newly introduced keys from .env.example without overwriting values
  python3 - <<'PY'
from pathlib import Path
import os
cfg = Path(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", str(Path.home()/".config")))) / "azeroth-moments" / ".env"
example = Path(os.environ["ROOT"]) / ".env.example"
existing = cfg.read_text(encoding="utf-8")
keys = {line.split("=",1)[0] for line in existing.splitlines() if line.strip() and not line.strip().startswith("#") and "=" in line}
additions = []
for line in example.read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    key = line.split("=",1)[0]
    if key not in keys:
        additions.append(line)
if additions:
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write("\n# Added by install.sh\n")
        fh.write("\n".join(additions) + "\n")
    print(f"Appended {len(additions)} new config keys to {cfg}")
PY
fi

cp "${ROOT}/packaging/azeroth-moments.service" "${UNIT_DIR}/azeroth-moments.service"
systemctl --user daemon-reload
systemctl --user enable azeroth-moments.service

if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" >/dev/null 2>&1 || true
fi

systemctl --user restart azeroth-moments.service || systemctl --user start azeroth-moments.service

echo
echo "Installed."
echo "  Config:  ${CONFIG_DIR}/.env"
echo "  Watch:   ${WATCH_DIR}"
echo "  Service: systemctl --user status azeroth-moments"
echo "  CLI:     ${INSTALL_DIR}/venv/bin/azeroth-moments status"
