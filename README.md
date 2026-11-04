# Daikin Madoka Bluetooth AC to Apple Home

Control Daikin Madoka / BRC1H Bluetooth wall controllers from the Apple Home app, including away from home. The Raspberry Pi acts as a small home-network relay: it talks Bluetooth to the Daikin remotes, and HomeBridge exposes them to Apple Home over Wi-Fi/Ethernet.

```text
iPhone Home app
  -> Apple TV / HomePod Home Hub
  -> HomeBridge on Raspberry Pi
  -> local daikin-web backend
  -> pymadoka
  -> Bluetooth LE
  -> Daikin Madoka controller
```

This repository is a field-tested reference implementation for people searching for Daikin Madoka Wi-Fi control, Daikin BRC1H HomeKit control, or a Raspberry Pi relay for Bluetooth-only Daikin AC remotes.

> **Do not port-forward this.** Remote access should happen through Apple HomeKit remote access via an Apple TV or HomePod Home Hub.

## Who this is for

Use this if:

- You have one or more Daikin Madoka / BRC1H Bluetooth controllers.
- You can control them locally over Bluetooth, but you want remote control from your iPhone.
- You are comfortable preparing a Raspberry Pi and following copy/paste terminal instructions.
- You have an Apple TV or HomePod that can act as an Apple Home Hub.

This is not an official Daikin project.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/daikin_web.py` | Local Python HTTP backend that serializes Bluetooth calls through `pymadoka`. |
| `homebridge-daikin-web/index.js` | HomeBridge platform plugin that exposes each AC as a HomeKit HeaterCooler accessory. |
| `systemd/daikin-web.service.example` | Hardened systemd unit for the backend. |
| `systemd/homebridge-hardening.conf` | Optional systemd hardening override for HomeBridge. |
| `config/daikin-web.env.example` | Backend environment configuration with placeholder MAC addresses. |
| `config/homebridge-platform.example.json` | HomeBridge platform config snippet. |
| `docs/` | Beginner-focused setup, pairing, security, troubleshooting, and operations docs. |
| `scripts/` | Helper scripts for status checks and basic diagnostics. |
| `PUBLICATION_CHECKLIST.md` | Final review checklist before pushing the repo public. |

## Start here

1. Read [`docs/01-overview.md`](docs/01-overview.md).
2. Prepare the hardware in [`docs/02-hardware.md`](docs/02-hardware.md).
3. Install the Pi software using [`docs/03-install-raspberry-pi.md`](docs/03-install-raspberry-pi.md).
4. Pair your Daikin controllers using [`docs/04-pair-controllers.md`](docs/04-pair-controllers.md).
5. Install the backend and HomeBridge plugin using [`docs/05-install-daikin-bridge.md`](docs/05-install-daikin-bridge.md).
6. Harden the Pi using [`docs/06-security-hardening.md`](docs/06-security-hardening.md).
7. Test Apple Home remote access using [`docs/07-apple-home-remote-access.md`](docs/07-apple-home-remote-access.md).
8. If something is slow or flaky, use [`docs/08-troubleshooting.md`](docs/08-troubleshooting.md).

## Expected behavior

Bluetooth is not instant. A healthy simple command is often **10-30 seconds**. A partial temperature change needs a live read before writing both thresholds and can take longer. The implementation is designed to prefer reliable control over a perfect real-time dashboard:

- One Bluetooth operation runs at a time.
- HomeKit writes get priority over background polling.
- Failed background status reads should not block turning an AC off.
- The backend is loopback-only by default.
- Unsent commands expire after 60 seconds, and a newer power request supersedes an older queued one. OFF gets priority over other queued writes, but cannot undo a command already sent over Bluetooth.
- Writes are not automatically replayed after a timeout: their outcome may be unknown. The plugin reads back the controller instead.
- HomeKit setters acknowledge receipt promptly, not physical completion. A failed write surfaces a communication error until a live read succeeds; a last good status is retained for at most five minutes through polling failures.
- Current temperature is the actual Madoka indoor/return-air reading, not the desired temperature. It can differ from a separate room thermometer.

The CLI supports **16-30 C temperature writes**. HomeKit can display higher readings from the physical controller, but the bridge rejects requests that would be silently clamped by `pymadoka`. See the [temperature limitations and upgrade notes](docs/08-troubleshooting.md#temperature-readings-and-limits).

For Sauter Cozytouch and Ecoso radiators alongside Daikin, see [adding other heaters to Apple Home](docs/07-apple-home-remote-access.md#adding-other-heaters-to-apple-home).

## Development checks

These tests use fake HomeKit services, local HTTP servers, and mocked Bluetooth subprocesses; they do not control real AC units or require extra packages:

```bash
python3 -m unittest discover -s tests -v
npm --prefix homebridge-daikin-web test
```

## Security defaults

This project assumes the Pi exists only as an AC bridge:

- Backend binds to `127.0.0.1:5050`, not the LAN.
- HomeBridge talks to the backend over localhost.
- Apple Home remote access goes through Apple TV / HomePod, not port forwarding.
- UFW should allow SSH only from your LAN, HomeBridge UI only from your LAN, plus HomeKit/mDNS.
- HomeBridge insecure mode should be disabled.
- Automatic updates should be enabled.

## Author

Created from a working home setup by [@HenryJammes](https://github.com/HenryJammes), with implementation assistance from GitHub Copilot.
