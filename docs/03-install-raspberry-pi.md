# 03 - Install Raspberry Pi software

These steps assume you are starting from a fresh Raspberry Pi OS Lite install.

## 1. Install Raspberry Pi OS

1. Install Raspberry Pi Imager on your computer.
2. Choose Raspberry Pi OS Lite 64-bit.
3. Enable SSH in the Imager settings.
4. Set a strong password.
5. Configure Wi-Fi if you will not use Ethernet.
6. Flash the card and boot the Pi.

## 2. SSH into the Pi

Replace `pi-hostname.local` with the hostname or IP shown by your router.

```bash
ssh YOUR_PI_USER@pi-hostname.local
```

## 3. Update the OS

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reconnect after reboot.

## 4. Install packages

```bash
sudo apt install -y \
  bluetooth bluez python3 python3-venv python3-pip \
  ca-certificates curl git gnupg ufw fail2ban unattended-upgrades
```

## 5. Install HomeBridge

Follow the official HomeBridge Raspberry Pi instructions when possible. This is the current APT-repository pattern:

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.homebridge.io/KEY.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/homebridge.gpg
echo "deb [signed-by=/etc/apt/keyrings/homebridge.gpg] https://repo.homebridge.io stable main" | sudo tee /etc/apt/sources.list.d/homebridge.list
sudo apt update
sudo apt install -y homebridge
```

Open the HomeBridge UI from your browser:

```text
http://PI_IP_ADDRESS:8581
```

Create an admin user and keep that password safe.

## 6. Install pymadoka

Install `pymadoka` from its upstream GitHub repository:

```bash
sudo mkdir -p /opt/pymadoka-venv
sudo chown "$USER":"$USER" /opt/pymadoka-venv
python3 -m venv /opt/pymadoka-venv
/opt/pymadoka-venv/bin/pip install --upgrade pip
/opt/pymadoka-venv/bin/pip install 'git+https://github.com/wmalgadey/pymadoka.git'
```

Check it exists:

```bash
/opt/pymadoka-venv/bin/pymadoka --help
```
