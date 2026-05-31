# 05 - Install the Daikin bridge

## 1. Copy backend files

```bash
sudo mkdir -p /opt/daikin-web
sudo cp src/daikin_web.py /opt/daikin-web/daikin_web.py
sudo chmod 0644 /opt/daikin-web/daikin_web.py
```

## 2. Create a service user

```bash
sudo useradd --system --home /opt/daikin-web --shell /usr/sbin/nologin --groups bluetooth daikin
sudo chown -R daikin:bluetooth /opt/daikin-web
```

If the user already exists, that is OK.

## 3. Configure devices

```bash
sudo cp config/daikin-web.env.example /etc/daikin-web.env
sudo nano /etc/daikin-web.env
```

Replace the sample MAC addresses with your real controller MACs.

## 4. Install the backend service

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

## 5. Install the HomeBridge plugin

From the repository root:

```bash
sudo mkdir -p /var/lib/homebridge/node_modules/homebridge-daikin-web
sudo cp homebridge-daikin-web/index.js homebridge-daikin-web/package.json /var/lib/homebridge/node_modules/homebridge-daikin-web/
sudo chown -R homebridge:homebridge /var/lib/homebridge/node_modules/homebridge-daikin-web
```

## 6. Configure HomeBridge

Open the HomeBridge UI:

```text
http://PI_IP_ADDRESS:8581
```

Edit `config.json` and add the platform from:

```text
config/homebridge-platform.example.json
```

Restart HomeBridge.

## 7. Add HomeBridge to Apple Home

In the Home app:

1. Tap `+`.
2. Add Accessory.
3. Scan the HomeBridge QR code or enter the HomeBridge PIN from the UI.
4. Assign each AC to the right room.

