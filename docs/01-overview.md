# 01 - Overview

Daikin Madoka / BRC1H controllers use Bluetooth Low Energy. Apple Home does not know how to talk to them directly, so this project inserts a Raspberry Pi bridge.

## Components

| Component | What it does |
| --- | --- |
| Daikin Madoka controller | The wall-mounted Bluetooth remote for the AC. |
| Raspberry Pi | Sits in Bluetooth range of the controllers. |
| BlueZ | Linux Bluetooth stack on the Pi. |
| `pymadoka` | Command-line tool that talks to Madoka over Bluetooth. |
| `daikin_web.py` | Local backend that turns HTTP requests into `pymadoka` commands. |
| HomeBridge | Bridges non-HomeKit devices into Apple Home. |
| `homebridge-daikin-web` | HomeBridge plugin in this repo. |
| Apple TV / HomePod | Apple Home Hub for remote access outside your home. |

## Why not expose the Pi directly?

Because the Pi is on your home network. Exposing a DIY HTTP endpoint to the internet creates unnecessary risk. Apple Home remote access already gives you a secure relay path through your Apple Home Hub.

## Realistic expectations

Bluetooth AC control is slower than Wi-Fi cloud devices:

- Local command: often 10-30 seconds.
- Remote cellular command through Apple Home: sometimes 30-75 seconds.
- Only one BLE command runs at a time.
- If a controller is at weak signal, it may occasionally timeout and retry later.

