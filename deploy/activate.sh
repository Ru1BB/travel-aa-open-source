#!/usr/bin/env bash
# Manually invoked after uploading one reviewed release. No Git or scheduled deploy.
set -euo pipefail
release=${1:?Provide an absolute release directory}
public_host=${2:?Provide the public IPv4 address or DNS hostname}
certificate_name=${3:-travel-aa-ip}
backend_port=${4:-5182}
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ $release =~ ^/srv/travel-aa/releases/[A-Za-z0-9._-]+$ ]] || { echo 'Invalid release directory' >&2; exit 1; }
[[ -f $release/web_server.py && -f $release/deploy/nginx.conf ]] || exit 1
rendered=$(mktemp -d)
trap 'rm -rf -- "$rendered"' EXIT
python3 "$release/deploy/render_config.py" "$rendered" --public-host "$public_host" --certificate-name "$certificate_name" --port "$backend_port"
[[ -f /etc/travel-aa-letsencrypt/live/$certificate_name/fullchain.pem ]] || { echo 'Install trusted TLS certificate first' >&2; exit 1; }
state=/srv/travel-aa/deploy-state/$(date -u +%Y%m%d-%H%M%S)
install -d -m 700 "$state"
cp -a /etc/nginx/sites-available/travel-aa "$state/nginx-before.conf"
readlink /srv/travel-aa/web-current > "$state/web-before.txt" || :
if [[ -f /etc/systemd/system/travel-aa.service ]]; then
    cp -a /etc/systemd/system/travel-aa.service "$state/service-before.unit"
fi
printf '%s\n' "$release" > "$state/release.txt"
id travel-aa >/dev/null 2>&1 || useradd --system --home-dir /var/lib/travel-aa --shell /usr/sbin/nologin travel-aa
install -d -o travel-aa -g travel-aa -m 700 /var/lib/travel-aa
chown -R root:root "$release"
find "$release" -type d -exec chmod 755 {} +
find "$release" -type f -exec chmod 644 {} +
ln -sfn "$release" /srv/travel-aa/web-current.next
mv -Tf /srv/travel-aa/web-current.next /srv/travel-aa/web-current
ln -sfn "$release" /srv/travel-aa/current.next
mv -Tf /srv/travel-aa/current.next /srv/travel-aa/current
install -m 644 "$rendered/travel-aa.service" /etc/systemd/system/travel-aa.service
systemctl daemon-reload
systemctl enable travel-aa.service
systemctl restart travel-aa.service
ready=0
for attempt in {1..20}; do
    if curl --fail --silent "http://127.0.0.1:$backend_port/api/me" > /dev/null; then ready=1; break; fi
    sleep 1
done
[[ $ready == 1 ]] || { echo "Backend failed. Rollback state: $state" >&2; exit 1; }
install -m 644 "$rendered/nginx.conf" /etc/nginx/sites-available/travel-aa
if ! nginx -t; then
    cp -a "$state/nginx-before.conf" /etc/nginx/sites-available/travel-aa
    echo "Nginx validation failed. Rollback state: $state" >&2
    exit 1
fi
systemctl reload nginx
install -m 644 "$release/deploy/travel-aa-cert-renew.service" /etc/systemd/system/travel-aa-cert-renew.service
install -m 644 "$release/deploy/travel-aa-cert-renew.timer" /etc/systemd/system/travel-aa-cert-renew.timer
systemctl daemon-reload
systemctl enable --now travel-aa-cert-renew.timer
printf 'Activated %s\nRollback state: %s\n' "$release" "$state"
