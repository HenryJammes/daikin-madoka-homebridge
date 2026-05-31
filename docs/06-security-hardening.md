# 06 - Security hardening

The Pi should be treated like an appliance, not a general-purpose server.

## 1. Do not port-forward

Do not expose SSH, HomeBridge UI, HomeKit, or `daikin-web` from your router.

## 2. Firewall

Replace `192.168.1.0/24` with your LAN subnet.

```bash
HAP_PORT=$(python3 -c 'import json; print(json.load(open("/var/lib/homebridge/config.json"))["bridge"]["port"])')

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp comment 'ssh LAN'
sudo ufw allow from 192.168.1.0/24 to any port 8581 proto tcp comment 'homebridge UI LAN'
sudo ufw allow "${HAP_PORT}/tcp" comment 'HomeKit HAP'
sudo ufw allow 5353/udp comment 'mDNS'
sudo ufw enable
sudo ufw status numbered
```

Do not add a rule for port `5050`. The backend should be reachable only from the Pi itself.

HomeBridge chooses its own HAP port in `config.json`; do not blindly copy `51127` from someone else's setup.

## 3. SSH

Recommended:

```bash
sudo sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\\?MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

SSH keys are safer than passwords. If you keep password auth, use a strong unique password and keep fail2ban enabled.

## 4. fail2ban

```bash
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

## 5. unattended upgrades

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

Review:

```bash
sudo unattended-upgrade --dry-run -d
```

## 6. Disable HomeBridge insecure mode

Create:

```bash
sudo tee /var/lib/homebridge/.uix-hb-service-homebridge-startup.json >/dev/null <<'EOF'
{
  "insecureMode": false,
  "debugMode": false,
  "keepOrphans": false,
  "env": {}
}
EOF
sudo chown homebridge:homebridge /var/lib/homebridge/.uix-hb-service-homebridge-startup.json
sudo chmod 0640 /var/lib/homebridge/.uix-hb-service-homebridge-startup.json
sudo systemctl restart homebridge
```

## 7. Optional HomeBridge systemd hardening

```bash
sudo mkdir -p /etc/systemd/system/homebridge.service.d
sudo cp systemd/homebridge-hardening.conf /etc/systemd/system/homebridge.service.d/10-hardening.conf
sudo systemctl daemon-reload
sudo systemctl restart homebridge
```

If HomeBridge fails to start, remove the override:

```bash
sudo rm /etc/systemd/system/homebridge.service.d/10-hardening.conf
sudo systemctl daemon-reload
sudo systemctl restart homebridge
```

## 8. Bluetooth visibility

```bash
bluetoothctl show
```

You want:

```text
Discoverable: no
Pairable: no
Discovering: no
```
