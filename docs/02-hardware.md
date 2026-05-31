# 02 - Hardware checklist

## Required

- Raspberry Pi 3, 4, or 5 with built-in Bluetooth. Pi 4/5 is the easiest recommendation; Pi 3 can work for this lightweight bridge.
- microSD card, 16 GB or larger.
- Bluetooth-capable Daikin Madoka / BRC1H controllers.
- Apple TV 4K / Apple TV HD or HomePod / HomePod mini signed into the same Apple Home.
- iPhone with the Home app.

You also need the normal basics to run a Pi: power, network access, and a way to flash Raspberry Pi OS.

## Nice to have

- Ethernet if convenient. Wi-Fi is fine as long as it is stable.
- A central Pi location with decent Bluetooth line-of-sight to the controllers.

## Placement

BLE signal matters. Put the Pi:

- Near the center of the apartment/house.
- Around the same height as the wall controllers if possible.
- Away from metal cabinets, electrical panels, and Wi-Fi routers.

RSSI guide:

| RSSI | Meaning |
| --- | --- |
| `-50` to `-65 dBm` | Strong |
| `-66` to `-75 dBm` | Usually fine |
| `-76` to `-85 dBm` | May work but can be flaky |
| below `-85 dBm` | Move the Pi or add a better Bluetooth adapter |
