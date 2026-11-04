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
`ok: true` means the HTTP process is alive, not that any controller is reachable.
The `devices` section reports each cached sample's age, verification status,
and last error without starting a Bluetooth operation.

## Check one room

```bash
curl --max-time 75 "http://127.0.0.1:5050/api/status?force=1&room=living_room"
```

This only reads status; it does not turn the AC on. Check `verified`, not just
HTTP 200. Older versions could return `ok: true, verified: false` with indefinitely
old data after a failed read. Version 0.2 returns HTTP 503 on read failure and
never treats an unverified sample as a fresh HomeKit status.

Read rooms one at a time: they share one Bluetooth adapter. A forced read bypasses
the failure cooldown; do not run it in a tight loop.

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
| Home shows the requested state but the unit did not change | Optimistic UI, failed BLE write, or older bridge swallowing CLI errors | Check write logs, then request a live read. Update backend and plugin together. |
| Command reports expired | It could not start/finish within its 60-second lifetime | Investigate BLE latency; explicitly send a new command if still wanted. |
| Repeated 31 C threshold warnings | A physical-controller reading exceeds the old HomeKit display bounds | Update the plugin; see temperature limits below. |

## Unexpected starts or changes

Keep the incident's approximate time and room, then compare the Homebridge and
backend logs:

```bash
grep -E 'HomeKit request|write /api/(power|mode|setpoint)|not replaying|expired' /var/lib/homebridge/homebridge.log
sudo journalctl -u daikin-web --since "2 hours ago" --no-pager
```

`HomeKit request` records receipt by the plugin; `write` records dispatch to the
backend. Neither identifies a household member or distinguishes Siri, a scene,
an automation, and manual use of Home. A logged ON request is not proof of an
unrequested start. A timeout is not proof that the unit stayed off, either.

Before version 0.2, the plugin retried a failed write once even if a newer
request had superseded it. Multiple queued operations could execute much later
than the original gesture. The new implementation drops superseded unsent
commands, prioritizes queued OFF requests, bounds the queue and command lifetime,
and never retries writes automatically. A command already in flight cannot be
recalled; use the physical controller if a timely OFF cannot be confirmed.

Also check Home automations, scenes, personal Shortcuts, and schedules/setback
settings on the Daikin controller or Madoka Assistant app. The bridge itself
does not schedule heating. Merely changing its displayed temperature should
not be confused with a compressor start: the current heating/cooling state is
derived from the enabled mode, not a separate compressor-running sensor.

## Bluetooth failures despite healthy services

Check `bluetoothctl show` and `bluetoothctl info YOUR_CONTROLLER_MAC`.
`Paired: yes` only describes the Pi's stored bond; it does not prove that the
controller still accepts it. `Connected: no` between operations is normal.

Close other Bluetooth clients such as Madoka Assistant. The upstream library
requires exclusive access to each controller. Check Pi placement, interference,
and whether only one room or every room fails. Do not repeatedly remove bonds,
restart services, or scan while commands are running.

If a Bluetooth-service restart is needed, arrange a maintenance window first.
If re-pairing is needed, follow [pairing](04-pair-controllers.md) with someone at
the wall controller. Do not reset the Apple Home bridge to fix a Bluetooth bond.

## Temperature readings and limits

Version 0.2 reports `temperatures.indoor` as Current Temperature. Earlier versions
reported the target setpoint instead, which could mislead temperature-based
automations. The Madoka sensor may be at the return-air inlet and differ from a
room thermometer; a separate room sensor is preferable for room-temperature
automations. Review existing automations before upgrading.

Some controllers report cooling targets of 31/32 C or heating targets of 31 C.
The HomeKit display accepts these values, but the upstream `pymadoka` 0.2.13 CLI
silently clamps **writes** above 30 C. This bridge deliberately rejects such
writes instead of pretending they succeeded. Supported bridge targets remain
16-30 C, subject to the controller's own configured limits.

A partial temperature request first reads the actual mode and both thresholds.
It refuses to overwrite an untouched threshold outside the CLI's supported
range. To proceed, intentionally adjust that threshold at the physical
controller, or explicitly supply both desired thresholds within 16-30 C:

```json
{"cooling": 25, "heating": 21}
```

This is a write payload example, not a diagnostic command. Do not apply it unless
both settings are intended. Devices that couple their heating and cooling
setpoints may still report different values after a write; the plugin performs
a live readback rather than keeping the optimistic values indefinitely.
