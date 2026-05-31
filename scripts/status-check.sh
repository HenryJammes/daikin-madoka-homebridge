#!/usr/bin/env bash
set -euo pipefail

echo "== services =="
systemctl is-active daikin-web homebridge fail2ban unattended-upgrades || true

echo
echo "== backend health =="
curl -fsS http://127.0.0.1:5050/healthz
echo

echo
echo "== listeners =="
ss -tlnp 2>/dev/null | awk 'NR==1 || $4 ~ /:22|:5050|:51127|:8581/'

echo
echo "== bluetooth =="
bluetoothctl show | grep -E 'Powered|Discovering|Pairable|Discoverable'

echo
echo "== firewall =="
sudo ufw status numbered

