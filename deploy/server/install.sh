#!/usr/bin/env bash
# Install or upgrade Cirqen in hospital server mode on Ubuntu 22.04 / 24.04.
#
#   sudo deploy/server/install.sh --server-name cirqen.hospital.local[,10.0.0.20] \
#        [--cert /path/cert.pem --key /path/key.pem]
#
# Safe to re-run: existing secrets, database and data are kept; code,
# dependencies, migrations, static files and service definitions are updated.
# See docs/SERVER_MODE.md.
set -euo pipefail

APP_DIR=/opt/cirqen/app
VENV=/opt/cirqen/venv
DATA_DIR=/var/lib/cirqen
ETC=/etc/cirqen
ENV_FILE=$ETC/cirqen.env
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

SERVER_NAMES=""
CERT=""
KEY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-name) SERVER_NAMES="$2"; shift 2 ;;
        --cert) CERT="$2"; shift 2 ;;
        --key) KEY="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }
if [[ -z "$SERVER_NAMES" && ! -f $ENV_FILE ]]; then
    echo "First install needs --server-name (the name(s) people will type in the browser)." >&2
    exit 2
fi
if [[ -n "$CERT" && -z "$KEY" ]] || [[ -z "$CERT" && -n "$KEY" ]]; then
    echo "--cert and --key go together." >&2; exit 2
fi

step() { printf '\n==> %s\n' "$*"; }
secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }

step "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql redis-server nginx python3-venv python3-dev \
    build-essential libpq-dev rsync openssl >/dev/null

step "Service account and folders"
id cirqen >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin cirqen
install -d -o cirqen -g cirqen -m 750 "$DATA_DIR" "$DATA_DIR/logs" "$DATA_DIR/backups"
install -d -o root -g cirqen -m 750 "$ETC" "$ETC/tls"
install -d -m 755 /opt/cirqen

step "Application code -> $APP_DIR"
rsync -a --delete --exclude .git --exclude data --exclude venv --exclude '__pycache__' \
    "$SRC_DIR/" "$APP_DIR/"
chown -R root:cirqen "$APP_DIR"
chmod -R g+rX,o-rwx "$APP_DIR"

step "Python environment"
[[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

step "Configuration ($ENV_FILE)"
if [[ ! -f $ENV_FILE ]]; then
    sed -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(secret)|" \
        -e "s|^POSTGRES_LOCAL_PASSWORD=.*|POSTGRES_LOCAL_PASSWORD=$(secret)|" \
        -e "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=$SERVER_NAMES|" \
        "$APP_DIR/deploy/server/cirqen.env.example" > "$ENV_FILE"
    echo "   Wrote $ENV_FILE with new secrets."
elif [[ -n "$SERVER_NAMES" ]]; then
    sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=$SERVER_NAMES|" "$ENV_FILE"
fi
chown root:cirqen "$ENV_FILE"
chmod 640 "$ENV_FILE"
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

step "PostgreSQL database"
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$POSTGRES_LOCAL_USER') THEN
    CREATE ROLE "$POSTGRES_LOCAL_USER" LOGIN PASSWORD '$POSTGRES_LOCAL_PASSWORD';
  ELSE
    ALTER ROLE "$POSTGRES_LOCAL_USER" WITH LOGIN PASSWORD '$POSTGRES_LOCAL_PASSWORD';
  END IF;
END \$\$;
SQL
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_LOCAL_DB'" | grep -q 1 \
    || sudo -u postgres createdb -O "$POSTGRES_LOCAL_USER" "$POSTGRES_LOCAL_DB"

step "Migrations and static files"
manage() { (cd "$APP_DIR" && sudo -u cirqen --preserve-env "$VENV/bin/python" manage.py "$@"); }
manage migrate --noinput
manage collectstatic --noinput --clear >/dev/null
manage check --deploy --fail-level ERROR

step "TLS certificate"
if [[ -n "$CERT" ]]; then
    install -o root -g root -m 644 "$CERT" "$ETC/tls/cirqen.crt"
    install -o root -g root -m 600 "$KEY" "$ETC/tls/cirqen.key"
elif [[ ! -f $ETC/tls/cirqen.crt ]]; then
    FIRST_NAME="${SERVER_NAMES%%,*}"
    SAN=$(echo "${SERVER_NAMES:-$DJANGO_ALLOWED_HOSTS}" | tr ',' '\n' | while read -r n; do
        [[ -z $n ]] && continue
        if [[ $n =~ ^[0-9.]+$ ]]; then echo "IP:$n"; else echo "DNS:$n"; fi
    done | paste -sd, -)
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=$FIRST_NAME" \
        -addext "subjectAltName=$SAN" \
        -keyout "$ETC/tls/cirqen.key" -out "$ETC/tls/cirqen.crt" 2>/dev/null
    chmod 600 "$ETC/tls/cirqen.key"
    echo "   Made a self-signed certificate. Replace it with one from the hospital's"
    echo "   certificate authority (re-run with --cert/--key) so browsers trust it."
fi

step "Services"
install -m 644 "$APP_DIR"/deploy/server/systemd/cirqen-*.service /etc/systemd/system/
NGINX_NAMES=$(echo "$DJANGO_ALLOWED_HOSTS" | tr ',' ' ')
sed "s|SERVER_NAMES|$NGINX_NAMES|g" "$APP_DIR/deploy/server/nginx/cirqen.conf" \
    > /etc/nginx/sites-available/cirqen
ln -sf /etc/nginx/sites-available/cirqen /etc/nginx/sites-enabled/cirqen
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl daemon-reload
systemctl enable --now postgresql redis-server >/dev/null
systemctl enable cirqen-web cirqen-worker cirqen-beat cirqen-sync >/dev/null
systemctl restart cirqen-web cirqen-worker cirqen-beat cirqen-sync
systemctl reload nginx

step "Health check"
for _ in $(seq 1 30); do
    if curl -fsk "https://127.0.0.1/health/" -H "Host: ${NGINX_NAMES%% *}" >/dev/null; then
        echo "   Cirqen is answering on https://${NGINX_NAMES%% *}/"
        break
    fi
    sleep 2
done

cat <<EOF

Done. Still to do by hand (docs/SERVER_MODE.md):
  * First login: sudo -u cirqen $VENV/bin/python $APP_DIR/manage.py create_hod \\
        --username <name> --email <email>     (with: set -a; source $ENV_FILE)
  * Link to HQ: put the enrollment code from Cirqen Labs in SYNC_ENROLLMENT_CODE
    in $ENV_FILE, then: systemctl restart cirqen-sync
  * Backups: set CIRQEN_BACKUP_COPY_DIR to a mounted share or disk.
EOF
