#!/usr/bin/env bash
# PostEveryday — installation sur un VPS propre Ubuntu/Debian.
# Version 1.1 — octobre 2026. Exécution par l'opérateur, pas par l'apprenant.
# Ce script est rangé dans le dépôt. Il installe le code du dépôt qui le contient.
# Usage : bash installer_vps.sh DOMAINE EMAIL --accepter-conditions-certificat
# L'accord aux conditions Let's Encrypt doit être donné avant l'exécution.
# Le DNS IPv4 doit pointer vers ce VPS ; aucun AAAA dans cette version.
# Ports 80 et 443 accessibles dans le pare-feu du fournisseur.
# Aucun mot de passe demandé : premier compte créé dans le navigateur.
set -Eeuo pipefail
umask 077
die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# == 3 ]] || die 'Paramètres : DOMAINE EMAIL --accepter-conditions-certificat'
SITE=$1
EMAIL=$2
[[ $3 == --accepter-conditions-certificat ]] || die 'Accord explicite aux conditions du certificat requis.'
[[ $(id -u) == 0 ]] || die 'Ce script doit être exécuté par l’opérateur en administrateur sur le VPS.'
[[ "$SITE" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$ && ${#SITE} -le 253 ]] || die 'Nom de domaine invalide.'
[[ "$EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}$ ]] || die 'Adresse électronique invalide.'
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$SOURCE/app.py" ]] || die 'Le script doit rester dans le dépôt, à côté de app.py.'
[[ -r /etc/os-release ]] || die 'Système non reconnu.'
. /etc/os-release
[[ "$ID" == ubuntu || "$ID" == debian ]] || die 'Ubuntu ou Debian requis.'
command -v apt-get >/dev/null || die 'Gestionnaire de paquets apt requis.'
command -v systemctl >/dev/null || die 'Gestionnaire de services systemd requis.'
command -v ss >/dev/null || die 'Commande ss requise pour contrôler les ports.'
command -v flock >/dev/null || die 'Commande flock requise.'
CODE=/opt/posteveryday
DONNEES=/var/lib/posteveryday
UNIT=/etc/systemd/system/posteveryday.service
CONF=/etc/nginx/sites-available/posteveryday
LIEN=/etc/nginx/sites-enabled/posteveryday
HOOK=/etc/letsencrypt/renewal-hooks/deploy/posteveryday-nginx.sh
ACME=/var/www/posteveryday-acme
export SITE CODE DONNEES
# Refuser un serveur déjà équipé : aucune application existante n'est écrasée.
for chemin in "$CODE" "$DONNEES" "$UNIT" /etc/nginx/nginx.conf /etc/letsencrypt/live/"$SITE" "$ACME"; do
  [[ ! -e "$chemin" ]] || die "Ce VPS n’est pas propre : $chemin existe déjà."
done
! id posteveryday >/dev/null 2>&1 || die 'Le compte système posteveryday existe déjà.'
! ss -H -ltn | awk '{print $4}' | grep -Eq ':(80|443|5010)$' || die 'Un port requis est occupé.'
exec 9>/run/lock/posteveryday-install.lock
flock -n 9 || die 'Une installation est déjà en cours.'
DEBUT=$(date +%s)
SERVICE_CREE=0
NGINX_INSTALLE=0
TMP=
rollback() {
  local statut=$?
  trap - ERR INT TERM
  set +e
  if (( SERVICE_CREE )); then systemctl disable --now posteveryday >/dev/null 2>&1; fi
  if (( NGINX_INSTALLE )); then systemctl stop nginx; fi
  if [[ -n "$TMP" ]]; then rm -rf -- "$TMP"; fi
  printf 'Installation interrompue ; les nouveaux services sont arrêtés.\n' >&2
  printf 'Fichiers conservés pour diagnostic. Aucun compte administrateur créé.\n' >&2
  exit "$statut"
}
trap rollback ERR
trap 'false' INT TERM
printf 'Installation des composants du VPS…\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# Nginx peut démarrer pendant l'installation du paquet : le signaler au rollback.
NGINX_INSTALLE=1
apt-get install -y -q python3 python3-venv python3-pip nginx certbot ffmpeg curl ca-certificates
systemctl stop nginx
# Vérifier le DNS contre les adresses configurées, avant toute émission de certificat.
python3 - "$SITE" <<'PY'
import json, socket, subprocess, sys
site = sys.argv[1]
ipv4 = {x[4][0] for x in socket.getaddrinfo(site, 80, socket.AF_INET)}
local = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr']))
addresses = {a['local'] for i in local for a in i.get('addr_info', [])}
if not ipv4 or not ipv4 <= addresses or '127.0.0.1' in ipv4:
    raise SystemExit('Le DNS IPv4 ne pointe pas uniquement vers une adresse de ce VPS. Arrêt.')
try:
    ipv6 = socket.getaddrinfo(site, 80, socket.AF_INET6)
except socket.gaierror:
    ipv6 = []
if ipv6:
    raise SystemExit('Retirer ou traiter l’enregistrement IPv6 avant cette installation IPv4.')
PY
# Contrôler que le dépôt est complet et ne contient aucune donnée privée.
python3 - "$SOURCE" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for name in ('app.py', 'depots.py', 'moteur.py', 'requirements.txt', 'test_installation.py', 'index.html'):
    if not (root / name).is_file():
        raise SystemExit('Dépôt incomplet : ' + name)
for p in root.rglob('*'):
    if '.git' in p.parts:
        continue
    if p.name in {'config.json', 'jeton_installation.txt', '.env'} or p.suffix in {'.db', '.sqlite', '.sqlite3'}:
        raise SystemExit('Le dépôt contient des données privées : ' + str(p.relative_to(root)))
PY
useradd --system --home-dir "$DONNEES" --shell /usr/sbin/nologin posteveryday
install -d -m 755 "$CODE"
tar -C "$SOURCE" --exclude=.git --exclude=venv -cf - . | tar -C "$CODE" -xf -
python3 -m venv "$CODE/venv"
"$CODE/venv/bin/python" -m pip install -q -r "$CODE/requirements.txt"
chmod -R a+rX "$CODE"
install -d -m 750 -o posteveryday -g posteveryday "$DONNEES" "$DONNEES/medias"
python3 - <<'PY'
import json, os, pwd, secrets
from pathlib import Path
p = Path(os.environ['DONNEES'])
u = pwd.getpwnam('posteveryday')
for name, content in [('config.json', json.dumps({'comptes': []})), ('jeton_installation.txt', secrets.token_urlsafe(48))]:
    f = p / name
    with f.open('x') as out:
        out.write(content)
    f.chmod(0o600)
    os.chown(f, u.pw_uid, u.pw_gid)
PY
printf 'Vérification de l’application…\n'
(cd "$CODE" && runuser -u posteveryday -- env PE_DONNEES="$DONNEES" "$CODE/venv/bin/python" -m unittest test_installation -q)
cat > "$UNIT" <<UNIT
[Unit]
Description=PostEveryday
After=network.target
[Service]
User=posteveryday
WorkingDirectory=$CODE
Environment=PE_DONNEES=$DONNEES
Environment=PE_BASE=https://$SITE
Environment=PE_IFRAME_ORIGINS=
ExecStart=$CODE/venv/bin/python -m gunicorn app:app --bind 127.0.0.1:5010 --workers 1 --threads 8 --timeout 600
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "$UNIT"
SERVICE_CREE=1
systemctl daemon-reload
systemctl enable --now posteveryday
for essai in {1..20}; do
  if curl -fsS --max-time 2 http://127.0.0.1:5010/api/sante >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS --max-time 5 http://127.0.0.1:5010/api/sante >/dev/null
install -d -m 755 "$ACME"
# Sauvegarder la configuration créée par le paquet avant son remplacement.
SAUVEGARDE=/root/sauvegarde_nginx_posteveryday_$(date +%Y%m%d_%H%M%S)
install -d -m 700 "$SAUVEGARDE"
cp -a /etc/nginx "$SAUVEGARDE/"
rm -f /etc/nginx/sites-enabled/default
cat > "$CONF" <<NGINX
server {
 listen 80;
 server_name $SITE;
 access_log off;
 error_log /var/log/nginx/posteveryday-error.log crit;
 location /.well-known/acme-challenge/ { root $ACME; }
 location / { return 503; }
}
NGINX
chmod 644 "$CONF"
ln -s "$CONF" "$LIEN"
nginx -t
systemctl enable --now nginx
printf 'Activation de HTTPS…\n'
certbot certonly --webroot -w "$ACME" -d "$SITE" -m "$EMAIL" \
  --agree-tos --no-eff-email --non-interactive
cat > "$CONF" <<NGINX
server {
 listen 80;
 server_name $SITE;
 access_log off;
 error_log /var/log/nginx/posteveryday-error.log crit;
 location /.well-known/acme-challenge/ { root $ACME; }
 location / { return 301 https://\$host\$request_uri; }
}
server {
 listen 443 ssl;
 server_name $SITE;
 ssl_certificate /etc/letsencrypt/live/$SITE/fullchain.pem;
 ssl_certificate_key /etc/letsencrypt/live/$SITE/privkey.pem;
 access_log off;
 error_log /var/log/nginx/posteveryday-error.log crit;
 client_max_body_size 500m;
 location / {
  proxy_pass http://127.0.0.1:5010;
  proxy_set_header Host \$host;
  proxy_set_header X-Forwarded-For \$remote_addr;
  proxy_set_header X-Forwarded-Proto \$scheme;
  proxy_read_timeout 600;
 }
}
NGINX
nginx -t
systemctl reload nginx
cat > "$HOOK" <<'HOOK'
#!/bin/sh
nginx -t && systemctl reload nginx
HOOK
chmod 700 "$HOOK"
systemctl enable --now certbot.timer
printf 'Vérification finale…\n'
for essai in {1..10}; do
  if curl -fsS --max-time 3 --resolve "$SITE:443:127.0.0.1" "https://$SITE/api/sante" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS --max-time 5 --resolve "$SITE:443:127.0.0.1" "https://$SITE/api/sante" >/dev/null
[[ $(curl -sS --max-time 5 --resolve "$SITE:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$SITE/") == 503 ]]
[[ $(curl -sS --max-time 5 --resolve "$SITE:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$SITE/installation") == 404 ]]
"$CODE/venv/bin/python" - <<'PY'
import os, urllib.request
from pathlib import Path
site = os.environ['SITE']
token = (Path(os.environ['DONNEES']) / 'jeton_installation.txt').read_text().strip()
try:
    with urllib.request.urlopen(f'https://{site}/installation?jeton={token}', timeout=10) as response:
        assert response.status == 200
except Exception:
    raise SystemExit('Le contrôle public du formulaire a échoué ; aucun jeton affiché.') from None
PY
trap - ERR INT TERM
printf 'Installation prête en %s secondes.\n' "$(( $(date +%s) - DEBUT ))"
printf 'Ouvrir le lien secret suivant et créer le premier compte dans le navigateur :\n'
python3 - <<'PY'
import os
from pathlib import Path
token = (Path(os.environ['DONNEES']) / 'jeton_installation.txt').read_text().strip()
print('https://' + os.environ['SITE'] + '/installation?jeton=' + token)
PY
