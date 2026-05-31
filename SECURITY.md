# Security policy

This project is intended for a Raspberry Pi on your home LAN.

## Do not expose the backend to the internet

Do **not** port-forward `5050`, `8581`, SSH, or HomeKit ports from your router.

Remote control should work through:

```text
iPhone Home app -> Apple Home remote access -> Apple TV / HomePod Home Hub -> HomeBridge
```

## Recommended posture

- Bind `daikin-web` to `127.0.0.1`.
- Keep `DAIKIN_HOST=127.0.0.1`.
- Use UFW.
- Allow SSH only from your LAN.
- Allow HomeBridge UI only from your LAN.
- Keep HomeBridge UI authentication enabled.
- Disable HomeBridge insecure mode.
- Enable unattended security updates.
- Disable SSH root login.
- Prefer SSH keys. If you keep password auth, use a strong unique password and fail2ban.

## Secrets and private data

Do not commit:

- Real `/etc/daikin-web.env`
- Real BLE MAC addresses if you consider them private
- SSH usernames/passwords
- HomeBridge pairing PINs
- HomeBridge `config.json` with secrets
- Any private SSH keys

