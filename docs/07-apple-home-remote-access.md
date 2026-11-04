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
5. Wait for the physical change. Healthy simple commands usually take 10-30 seconds; unsent commands expire after 60 seconds rather than replaying much later.
6. Turn Wi-Fi back on.

If it only works on Wi-Fi, the Apple Home Hub is not connected or HomeBridge is not properly paired.

## Adding other heaters to Apple Home

Apple Home can combine multiple bridges. Keep the existing Daikin accessories
in Homebridge while adding radiators through a compatible integration; there
is no need to re-pair the ACs or move the entire installation.

Identify each radiator's exact model, controlling app, and any gateway first.
A brand name or a screenshot of its temperature dial does not establish
HomeKit, Matter, or Tuya compatibility.

### Sauter Cozytouch

For **Overkiz-compatible Cozytouch devices**, the existing Homebridge installation
can use [`homebridge-tahoma`](https://github.com/dubocr/homebridge-tahoma) with
service `cozytouch`. The plugin explicitly lists Sauter, Atlantic, and Thermor.
Configure credentials privately in Homebridge, preferably isolate the plugin
in a child bridge, and verify that the particular radiator is exposed as a
thermostat before relying on it.

Alternatively, Home Assistant's built-in
[`Overkiz` integration](https://www.home-assistant.io/integrations/overkiz/)
lists Sauter Cozytouch. Not all devices sold under Cozytouch use Overkiz.
For hardware absent from Overkiz, the community
[`Atlantic Cozytouch` integration](https://github.com/gduteil/cozytouch)
targets a different Atlantic cloud API and includes Sauter support. Exact
capability mapping still needs checking for the model; this is a custom
integration, not guaranteed native support.

Do not buy a TaHoma/Cozytouch gateway or assume a local-only connection solely
from the brand. Wi-Fi and gateway-based models differ. The official Overkiz
documentation lists which gateways support its local API and notes that climate
entities are not supported over that API.

### Ecoso Wi-Fi elements and radiators

The [Ecoso Wi-Fi element manual](https://cdn.bestheating.com/pdfs/ECOSO_WIFI_Element_Manual_Universal_UK.pdf)
directs users to the **ECOSO app**. Some other Ecoso models use Smart Life.
Do not infer that a device in the branded app can be linked through Smart Life
without confirming its exact model and account/pairing support.

If the device is supported and paired in **Smart Life or Tuya Smart**, Home
Assistant's built-in [`Tuya` integration](https://www.home-assistant.io/integrations/tuya/)
is a documented route. Its current setup uses the app's User Code and QR-code
authorization. A developer-cloud subscription is not part of that setup.
Not every vendor datapoint or mode is exposed by Tuya's SDK, so check that
power, target temperature, current temperature, and heat/off are all available.

If it only works in ECOSO, confirm compatibility with the manufacturer before
resetting or re-pairing it. Do not promise full thermostat support based on a
similar-looking Tuya interface.

### Exposing Home Assistant radiators in Home

Once a radiator works correctly in Home Assistant, add
[`HomeKit Bridge`](https://www.home-assistant.io/integrations/homekit/) (not
HomeKit Device), include only the intended radiator climate entities, and add
that bridge's QR code to Apple Home. Assign the radiators to their rooms.
Basic on/off and temperature control can then share the same Home app as Daikin.
Vendor-specific Boost, holiday, program, and energy features may remain in the
vendor app or Home Assistant.

Do not expose an accessory through two bridges at once. Check native heater
schedules as well as Home automations so a vendor program does not unexpectedly
override a Home setting. Avoid mains smart-plug workarounds for fixed heaters:
use the supported control interface and retain the manufacturer's protections.

Home Assistant is an additional service, not a required replacement for this
Pi image. Assess resources and backups before installing it alongside
Homebridge, or use a separate host. Do not reflash a working bridge to add one
integration.
