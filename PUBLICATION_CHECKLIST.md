# Publication checklist

Before making this repository public, run through this list.

## Secret/private-data checks

Search for real values from your own setup:

```bash
grep -RInE 'YOUR_REAL_PI_USER|YOUR_REAL_PASSWORD|YOUR_REAL_HOMEKIT_PIN|YOUR_REAL_PI_IP|YOUR_REAL_BLE_MAC' .
```

Also search for common accidental leaks:

```bash
grep -RInE 'password|passwd|secret|SSH key material|HomeKit|pin|192\.168\.|10\.0\.|172\.16\.' .
```

Expected results:

- Generic documentation references to passwords are OK.
- Placeholder values like `PI_IP_ADDRESS`, `YOUR_PI_USER`, and `AA:BB:CC:DD:EE:01` are OK.
- Real Pi usernames, real passwords, real SSH keys, real HomeKit PINs, real controller MAC addresses, and real local IP addresses are **not** OK.

## Functional checks

From the repo root:

```bash
python3 -m py_compile src/daikin_web.py
node --check homebridge-daikin-web/index.js
```

## Suggested GitHub repo

Repository name:

```text
daikin-madoka-homebridge
```

Description:

```text
Control Daikin Madoka Bluetooth AC controllers from Apple Home using Raspberry Pi, HomeBridge, and an Apple TV/HomePod Home Hub.
```

Topics:

```text
daikin, madoka, brc1h, homebridge, homekit, raspberry-pi, bluetooth, ble, hvac
```

## Publish with GitHub CLI

Only run this after reviewing the files:

```bash
git init -b main
git add .
git commit -m "Initial Daikin Madoka HomeBridge bridge

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
gh repo create HenryJammes/daikin-madoka-homebridge --public --source . --remote origin --push
```
