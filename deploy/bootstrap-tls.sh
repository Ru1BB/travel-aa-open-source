#!/usr/bin/env bash
# Manual initial TLS setup. Private certificate/account keys remain on this server.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ -x /opt/travel-aa-certbot/bin/certbot ]] || { echo 'Install Certbot >= 5.4 in /opt/travel-aa-certbot first' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
public_ip=${1:?Provide your public IPv4 address}
python3 -c 'import ipaddress,sys; assert ipaddress.ip_address(sys.argv[1]).version == 4' "$public_ip"
rendered=$(mktemp -d)
trap 'rm -rf -- "$rendered"' EXIT
python3 "$source_dir/render_config.py" "$rendered" --public-host "$public_ip"
install -d -m 755 /var/lib/travel-aa-acme/.well-known/acme-challenge
install -d -m 700 /etc/travel-aa-letsencrypt /var/lib/travel-aa-certbot /var/log/travel-aa-certbot
if [[ ! -e /srv/travel-aa/nginx-before-tls.conf ]]; then
    cp -a /etc/nginx/sites-available/travel-aa /srv/travel-aa/nginx-before-tls.conf
fi
install -m 644 "$rendered/nginx-acme.conf" /etc/nginx/sites-available/travel-aa
if ! nginx -t; then
    cp -a /srv/travel-aa/nginx-before-tls.conf /etc/nginx/sites-available/travel-aa
    exit 1
fi
systemctl reload nginx
options=(--non-interactive --agree-tos --register-unsafely-without-email --webroot --webroot-path /var/lib/travel-aa-acme --ip-address "$public_ip" --preferred-profile shortlived --cert-name travel-aa-ip --config-dir /etc/travel-aa-letsencrypt --work-dir /var/lib/travel-aa-certbot --logs-dir /var/log/travel-aa-certbot)
/opt/travel-aa-certbot/bin/certbot certonly --dry-run "${options[@]}"
/opt/travel-aa-certbot/bin/certbot certonly "${options[@]}"
openssl x509 -in /etc/travel-aa-letsencrypt/live/travel-aa-ip/fullchain.pem -noout -subject -issuer -dates
