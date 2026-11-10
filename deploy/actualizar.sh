#!/usr/bin/env bash
# Publica la última versión de main:  sudo bash /opt/rfconecta/deploy/actualizar.sh
set -euo pipefail
APP=/opt/rfconecta
sudo -u rf git -C "$APP" pull --ff-only origin main
sudo -u rf "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
systemctl restart rfconecta
sleep 2 && systemctl is-active --quiet rfconecta && echo "Actualizado y funcionando." || { journalctl -u rfconecta -n 30 --no-pager; exit 1; }
