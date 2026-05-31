# 07 - Apple Home remote access

Remote access does not use the Pi directly. It uses Apple Home.

## Requirements

- Apple TV 4K / Apple TV HD, HomePod, or HomePod mini at home.
- Same Apple Home as your iPhone.
- HomeBridge added to Apple Home.
- No router port forwarding.

## Check the Home Hub

On iPhone:

1. Open Home.
2. Tap the house icon.
3. Tap Home Settings.
4. Tap Home Hubs & Bridges.
5. Confirm your Apple TV or HomePod says `Connected`.

## Test cellular control

1. Turn iPhone Wi-Fi off.
2. Confirm the phone is on cellular.
3. Open Home.
4. Toggle one AC or change the setpoint.
5. Wait. Bluetooth commands can take 30-75 seconds end to end.
6. Turn Wi-Fi back on.

If it only works on Wi-Fi, the Apple Home Hub is not connected or HomeBridge is not properly paired.

