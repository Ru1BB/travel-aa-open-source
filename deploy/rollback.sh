#!/usr/bin/env bash
# Manual rollback. Persistent account/ledger data is never deleted or replaced.
set -euo pipefail
state=${1:?Provide the recorded deployment state directory}
[[ $EUID == 0 ]] || exit 1
[[ $state =~ ^/srv/travel-aa/deploy-state/[0-9]{8}-[0-9]{6}$ ]] || exit 1
[[ -f $state/nginx-before.conf ]] || exit 1
previous=$(cat "$state/web-before.txt")
if [[ -n $previous ]]; then
    [[ $previous =~ ^/srv/travel-aa/releases/[A-Za-z0-9._-]+$ && -f $previous/web_server.py ]] || exit 1
    ln -sfn "$previous" /srv/travel-aa/web-current.next
    mv -Tf /srv/travel-aa/web-current.next /srv/travel-aa/web-current
    [[ ! -f $state/service-before.unit ]] || cp -a "$state/service-before.unit" /etc/systemd/system/travel-aa.service
    systemctl daemon-reload
    systemctl restart travel-aa.service
fi
cp -a "$state/nginx-before.conf" /etc/nginx/sites-available/travel-aa
nginx -t
systemctl reload nginx
if [[ -z $previous ]]; then systemctl disable --now travel-aa.service; fi
printf 'Rolled back using %s; /var/lib/travel-aa retained.\n' "$state"
