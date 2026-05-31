# 02 - Hardware checklist

## Required

- Raspberry Pi 4 or Raspberry Pi 5.
- Official or good-quality USB-C power supply.
- microSD card, 16 GB or larger.
- Bluetooth-capable Daikin Madoka / BRC1H controllers.
- Apple TV 4K / Apple TV HD or HomePod / HomePod mini signed into the same Apple Home.
- iPhone with the Home app.

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

