# 08 - Troubleshooting

## Check services

```bash
systemctl is-active daikin-web homebridge fail2ban unattended-upgrades
```

## Check backend health

```bash
curl http://127.0.0.1:5050/healthz
```

This should work on the Pi. It should **not** work from another LAN device.

## Check one room

```bash
curl "http://127.0.0.1:5050/api/status?force=1&room=living_room"
```

## Watch backend logs

```bash
sudo journalctl -u daikin-web -f
```

## Watch HomeBridge logs

```bash
sudo tail -f /var/lib/homebridge/homebridge.log
```

## Stop accidental Bluetooth scanning

```bash
bluetoothctl scan off
bluetoothctl show
```

Make sure `Discovering: no`.

## Measure signal

```bash
timeout 20s bluetoothctl scan le
bluetoothctl scan off
```

Look for RSSI lines. Around `-65` to `-75 dBm` is usually workable. Around `-80 dBm` or worse can be flaky.

## Common symptoms

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Home app says No Response | HomeBridge down, Home Hub offline, or network issue | Check HomeBridge and Apple Home Hub status. |
| Command takes 60+ seconds | BLE timeout/retry or weak signal | Move Pi, stop scanning, check RSSI. |
| One room is flaky | Weak BLE or pairing issue | Re-pair that controller and move Pi closer. |
| Backend returns 503 | `pymadoka` failed or device cooldown | Check logs and retry after cooldown. |
| Works on Wi-Fi but not cellular | Apple Home Hub not connected | Check Home Hubs & Bridges in the Home app. |

