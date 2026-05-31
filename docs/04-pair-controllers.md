# 04 - Pair the Daikin controllers

Pairing is the hardest part. Do one controller at a time.

## 1. Find your controller MAC address

Run:

```bash
bluetoothctl
power on
scan le
```

Look for devices that appear when the Madoka controller is awake. Note the MAC address, which looks like:

```text
AA:BB:CC:DD:EE:01
```

Stop scanning when done:

```bash
scan off
quit
```

## 2. Pair

Run:

```bash
bluetoothctl
power on
agent KeyboardDisplay
default-agent
pair AA:BB:CC:DD:EE:01
trust AA:BB:CC:DD:EE:01
quit
```

If a passkey appears:

1. Confirm the number on the wall controller.
2. Confirm it in the terminal.

## 3. Test pymadoka

```bash
/opt/pymadoka-venv/bin/pymadoka -a AA:BB:CC:DD:EE:01 -d hci0 --clean get-status
```

If this returns JSON, pairing works.

Repeat for each controller.

