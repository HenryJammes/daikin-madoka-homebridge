# 05 - Install the Daikin bridge

## 1. Clone this repository on the Pi

```bash
cd ~
git clone https://github.com/HenryJammes/daikin-madoka-homebridge.git
cd daikin-madoka-homebridge
```

All following commands assume you are in this repository directory.

## 2. Copy backend files

```bash
sudo mkdir -p /opt/daikin-web
sudo cp src/daikin_web.py /opt/daikin-web/daikin_web.py
sudo chmod 0644 /opt/daikin-web/daikin_web.py
```

## 3. Create a service user

```bash
sudo groupadd --system daikin || true
sudo useradd --system --gid daikin --groups bluetooth --home-dir /opt/daikin-web --shell /usr/sbin/nologin daikin || true
sudo chown -R daikin:bluetooth /opt/daikin-web
```

If the user already exists, that is OK.

## 4. Configure devices

```bash
sudo cp config/daikin-web.env.example /etc/daikin-web.env
sudo nano /etc/daikin-web.env
```

Replace the sample MAC addresses with your real controller MACs.

## 5. Install the backend service

```bash
sudo cp systemd/daikin-web.service.example /etc/systemd/system/daikin-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now daikin-web
```

Check it:

```bash
systemctl status daikin-web --no-pager
curl http://127.0.0.1:5050/healthz
```

## 6. Install the HomeBridge plugin

From the repository root:

```bash
sudo mkdir -p /var/lib/homebridge/node_modules/homebridge-daikin-web
sudo cp homebridge-daikin-web/index.js homebridge-daikin-web/package.json /var/lib/homebridge/node_modules/homebridge-daikin-web/
sudo chown -R homebridge:homebridge /var/lib/homebridge/node_modules/homebridge-daikin-web
```

## 7. Configure HomeBridge

Open the HomeBridge UI:

```text
http://PI_IP_ADDRESS:8581
```

Edit `config.json` and add the platform from:

```text
config/homebridge-platform.example.json
```

Restart HomeBridge.

## 8. Add HomeBridge to Apple Home

In the Home app:

1. Tap `+`.
2. Add Accessory.
3. Scan the HomeBridge QR code or enter the HomeBridge PIN from the UI.
4. Assign each AC to the right room.

## Updating an existing installation

Back up the installed backend, plugin files, and local configuration first.
Check the actual backend location with:

```bash
systemctl show --no-pager daikin-web -p ExecStart -p EnvironmentFiles
```

Early installations may run from a user's home directory rather than
`/opt/daikin-web`. Do not replace such an installation blindly: move any
hard-coded device list into `DAIKIN_DEVICES` in the service's environment file
first. Keep the existing room IDs, MACs, service user, virtual environment path,
and Homebridge configuration. Never replace them with the example devices.

Update the backend and both plugin files together, then restart `daikin-web`
and `homebridge` during a maintenance window. Restarting discards unsent
commands; it does not intentionally turn any units on or off. Do not remove
the Homebridge bridge from Apple Home or clear its accessory cache.

Version 0.2 changes error reporting and reports the real indoor sensor instead
of mirroring a setpoint. Review any temperature-based Home automations before
deploying. Use the read-only checks in [troubleshooting](08-troubleshooting.md)
first; test a physical power or temperature change only when someone is ready
to observe the intended result.
