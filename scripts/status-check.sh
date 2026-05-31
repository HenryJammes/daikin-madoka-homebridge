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
HAP_PORT="$(python3 -c 'import json; print(json.load(open("/var/lib/homebridge/config.json")).get("bridge", {}).get("port", ""))' 2>/dev/null || true)"
if [ -n "${HAP_PORT}" ]; then
  ss -tlnp 2>/dev/null | awk -v hap=":${HAP_PORT}" 'NR==1 || $4 ~ /:22|:5050|:8581/ || index($4, hap)'
else
  ss -tlnp 2>/dev/null | awk 'NR==1 || $4 ~ /:22|:5050|:8581/'
fi

echo
echo "== bluetooth =="
bluetoothctl show | grep -E 'Powered|Discovering|Pairable|Discoverable'

echo
echo "== firewall =="
sudo ufw status numbered
