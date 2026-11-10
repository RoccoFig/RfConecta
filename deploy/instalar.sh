#!/usr/bin/env bash
# Instala RF en un VPS nuevo con Ubuntu 24.04. Ejecutar como root, una sola vez:
#   sudo bash deploy/instalar.sh rfconecta.cloud
# Antes: el dominio tiene que apuntar (registro A) a la IP del servidor, y el repo tiene que estar en /opt/rfconecta.
set -euo pipefail
DOMINIO="${1:?Uso: sudo bash deploy/instalar.sh <dominio>}"
APP=/opt/rfconecta
[ -f "$APP/app.py" ] || { echo "No encuentro $APP/app.py: cloná el repo en $APP primero."; exit 1; }

echo "== Paquetes y actualizaciones automáticas de seguridad"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip git ufw unattended-upgrades sqlite3 \
  debian-keyring debian-archive-keyring apt-transport-https curl gnupg
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Firewall: solo SSH, HTTP y HTTPS"
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

echo "== Usuario sin privilegios para la app y carpeta de datos"
id rf &>/dev/null || useradd --system --home "$APP" --shell /usr/sbin/nologin rf
mkdir -p /var/data/respaldos
chown -R rf:rf /var/data "$APP"
chmod 750 /var/data

echo "== Entorno de Python"
sudo -u rf python3 -m venv "$APP/.venv"
sudo -u rf "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "== Variables de entorno (/etc/rfconecta.env)"
if [ ! -f /etc/rfconecta.env ]; then
  cat > /etc/rfconecta.env <<VARS
PRODUCCION=1
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
ADMIN_PASSWORD=CAMBIAR
PUBLIC_BASE_URL=https://$DOMINIO
DB_PATH=/var/data/puntos.db
SDM_META_KEY=00000000000000000000000000000000
SDM_FILE_KEY=00000000000000000000000000000000
VARS
  chmod 640 /etc/rfconecta.env && chown root:rf /etc/rfconecta.env
  echo "   Creado. Editá ADMIN_PASSWORD (y luego las claves SDM) con: sudo nano /etc/rfconecta.env"
fi

echo "== Servicio de la app"
cp "$APP/deploy/rfconecta.service" /etc/systemd/system/rfconecta.service
systemctl daemon-reload && systemctl enable --now rfconecta

echo "== Caddy (HTTPS automático)"
if ! command -v caddy &>/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi
sed "s/DOMINIO/$DOMINIO/" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo "== Tareas programadas: respaldo diario y mensajes automáticos"
cat > /etc/cron.d/rfconecta <<CRON
SHELL=/bin/bash
# Respaldo diario a las 4:00 (hora del servidor), conserva 30 copias
0 4 * * * rf set -a; . /etc/rfconecta.env; cd $APP && .venv/bin/python tools/respaldar.py /var/data/respaldos 30 >> /var/data/respaldos/log.txt 2>&1
# Mensajes automáticos cada hora (respetan su propio horario de envío).
# Desactivado hasta que WhatsApp mande mensajes reales (hoy solo salen por consola):
# 15 * * * * rf set -a; . /etc/rfconecta.env; cd $APP && .venv/bin/python automatizaciones.py >> /var/data/automatizaciones.log 2>&1
CRON

echo
echo "Listo. Probá: https://$DOMINIO/salud"
echo "Pendiente: editar ADMIN_PASSWORD en /etc/rfconecta.env y ejecutar: sudo systemctl restart rfconecta"
