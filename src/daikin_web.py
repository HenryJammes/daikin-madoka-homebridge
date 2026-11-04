#!/usr/bin/env python3
"""Multi-room Daikin Madoka (BRC1H) web controller.

Drives several Madoka controllers from one Pi over BLE via the `pymadoka` CLI.
A single BLE radio is shared, so all calls are serialized through one lock.
"""
import copy
import json
import math
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HOST = os.environ.get("DAIKIN_HOST", "127.0.0.1")
PORT = int(os.environ.get("DAIKIN_WEB_PORT", "5050"))
ADAPTER = os.environ.get("DAIKIN_ADAPTER", "hci0")
PYMADOKA = os.environ.get(
    "PYMADOKA_BIN",
    str(Path.home() / "pymadoka-venv" / "bin" / "pymadoka"),
)

# Order here drives the order of the tab strip in the UI.
# Replace these examples with your own controller IDs, names, and BLE MACs.
# Production installs should normally set DAIKIN_DEVICES in /etc/daikin-web.env.
# JSON shape: [{"id":"living_room","name":"Living room","mac":"AA:BB:CC:DD:EE:01"}]
DEFAULT_DEVICES = [
    {"id": "living_room", "name": "Living room", "mac": "AA:BB:CC:DD:EE:01"},
    {"id": "bedroom",     "name": "Bedroom",     "mac": "AA:BB:CC:DD:EE:02"},
]


def load_devices():
    raw = os.environ.get("DAIKIN_DEVICES")
    if not raw:
        return list(DEFAULT_DEVICES)
    devices = json.loads(raw)
    for d in devices:
        if not all(k in d for k in ("id", "name", "mac")):
            raise SystemExit(f"DAIKIN_DEVICES entry missing id/name/mac: {d}")
    return devices


DEVICES = load_devices()
DEVICE_BY_ID = {d["id"]: d for d in DEVICES}

BLE_LOCK = threading.Lock()
# Guards STATUS_CACHE and FAILURES mutations against concurrent
# ThreadingHTTPServer workers. CPython's GIL makes single dict ops atomic, but
# check-then-act sequences (get_status, ble_write post-update) are not.
STATE_LOCK = threading.Lock()

# Per-room cache so flipping between tabs doesn't trigger a fresh BLE call every time.
STATUS_CACHE = {}      # id -> {"data": dict, "ts": float}
STATUS_TTL = float(os.environ.get("DAIKIN_STATUS_TTL", "60"))

# Per-room failure cooldown so an unpaired or unreachable controller can't keep
# the shared BLE adapter pinned for every tab switch.
FAILURES = {}          # id -> {"ts": float, "code": str, "message": str}
FAILURE_COOLDOWN = float(os.environ.get("DAIKIN_FAILURE_COOLDOWN", "120"))

# pymadoka per-call timeout (s). A healthy Madoka round-trip is typically
# 15-25s here. Keep the timeout short enough that one bad poll cannot pin the
# single BLE adapter for more than a minute while HomeKit writes wait behind it.
PYMADOKA_TIMEOUT = float(os.environ.get("DAIKIN_PYMADOKA_TIMEOUT", "40"))
# Extra attempts on transient BlueZ errors before surfacing failure (which would
# then trigger the per-device cooldown). Default 0 keeps end-to-end latency under
# the HomeBridge plugin's 75 s HTTP timeout even on a cold cache. Set to 1 if you
# prefer resilience over latency.
PYMADOKA_RETRIES = int(os.environ.get("DAIKIN_PYMADOKA_RETRIES", "0"))
# Backoff between retry attempts (s). Short — most transient errors clear quickly.
PYMADOKA_RETRY_BACKOFF = float(os.environ.get("DAIKIN_PYMADOKA_RETRY_BACKOFF", "2.0"))
# Sleep inside BLE_LOCK after every run_pymadoka invocation so BlueZ can release
# the BLE connection before the next caller's subprocess starts. Empirically ~1s
# is enough; retries inside a single invocation use PYMADOKA_RETRY_BACKOFF instead.
POST_CALL_SETTLE = float(os.environ.get("DAIKIN_POST_CALL_SETTLE", "1.0"))

# Cap on Content-Length for POST bodies. Real payloads are <100 bytes; this just
# stops a malicious/buggy LAN client from tying up a worker thread reading a
# multi-megabyte body. Returns 413 when exceeded.
MAX_POST_BODY = int(os.environ.get("DAIKIN_MAX_POST_BODY", "4096"))
REQUEST_TIMEOUT = float(os.environ.get("DAIKIN_REQUEST_TIMEOUT", "10"))
REQUEST_QUEUE_SIZE = int(os.environ.get("DAIKIN_REQUEST_QUEUE_SIZE", "16"))
WRITE_TIMEOUT = 60.0


def classify_error(message):
    text = (message or "").lower()
    pairing_signals = (
        "not paired", "authentication", "le-connection-abort",
        "br-connection-canceled", "page-timeout", "did not respond",
        "connection refused", "host is down", "no route to host",
        "device not available", "operation already in progress",
    )
    if any(sig in text for sig in pairing_signals):
        return "unreachable"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "error"


# Transient BlueZ failures that are worth one quick retry inside run_pymadoka
# before we burn the per-device cooldown. Permanent issues like "not paired" or
# "not found" are excluded — retrying those just wastes the BLE radio.
_RETRYABLE_SIGNALS = (
    "le-connection-abort", "br-connection-canceled", "page-timeout",
    "did not respond", "operation already in progress",
    "device not available", "host is down", "in progress",
    "timed out", "timeout",
)


def _is_retryable(message):
    text = (message or "").lower()
    return any(sig in text for sig in _RETRYABLE_SIGNALS)


def parse_setpoint(value, label):
    """Strictly parse a HomeKit/UI setpoint.

    JSON booleans are `int` subclasses in Python, so reject them before numeric
    coercion. pymadoka's CLI silently clamps writes above 30 C.
    """
    if isinstance(value, bool):
        raise ValueError(f"{label} setpoint must be an integer between 16 and 30")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{label} setpoint must be an integer between 16 and 30")
        parsed = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdecimal():
            raise ValueError(f"{label} setpoint must be an integer between 16 and 30")
        parsed = int(stripped)
    else:
        raise ValueError(f"{label} setpoint must be an integer between 16 and 30")
    if parsed < 16 or parsed > 30:
        raise ValueError(f"{label} setpoint must be between 16 and 30; pymadoka cannot preserve values outside this range")
    return parsed

HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="AC">
  <title>AC</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f7f4;
      --ink: #181915;
      --muted: #6c6f68;
      --line: #d7d8d1;
      --panel: #ffffff;
      --accent: #0e7a7a;
      --accent-ink: #ffffff;
      --warn: #b7492a;
      --shadow: 0 10px 26px rgba(24, 25, 21, 0.08);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #11130f;
        --ink: #f1f2ed;
        --muted: #a5a99f;
        --line: #2c3028;
        --panel: #1a1d18;
        --shadow: 0 12px 30px rgba(0, 0, 0, 0.3);
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 16px/1.35 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(720px, 100%);
      margin: 0 auto;
      padding: 14px 14px 32px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    h1 {
      margin: 0;
      font-size: clamp(24px, 6vw, 36px);
      font-weight: 760;
      letter-spacing: 0;
    }
    .status {
      color: var(--muted);
      font-size: 14px;
      min-height: 20px;
    }
    .rooms {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 4px 0 14px;
    }
    .rooms button {
      flex: 1 1 auto;
      min-height: 40px;
      padding: 0 12px;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      border-radius: 999px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }
    .rooms button.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--accent-ink);
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
      margin: 12px 0;
    }
    .hero {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }
    .temp {
      font-size: clamp(48px, 18vw, 86px);
      line-height: 0.95;
      font-weight: 780;
    }
    .sub { color: var(--muted); margin-top: 6px; }
    .power {
      width: 72px;
      height: 72px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      font-size: 28px;
      line-height: 1;
    }
    .power.on {
      border-color: var(--accent);
      background: var(--accent);
      color: var(--accent-ink);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .label {
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }
    .seg {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
    }
    button.act {
      appearance: none;
      min-height: 44px;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      border-radius: 8px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button.act.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--accent-ink);
    }
    button:disabled { opacity: 0.55; cursor: wait; }
    .stepper {
      display: grid;
      grid-template-columns: 52px 1fr 52px;
      align-items: center;
      gap: 8px;
    }
    .stepper button {
      appearance: none;
      min-height: 44px;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      border-radius: 8px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    .setpoint {
      text-align: center;
      font-size: 32px;
      font-weight: 760;
    }
    .details {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      color: var(--muted);
      font-size: 14px;
    }
    .value {
      display: block;
      color: var(--ink);
      font-size: 18px;
      margin-top: 2px;
    }
    .warn { color: var(--warn); }
    @media (max-width: 520px) {
      .grid, .details { grid-template-columns: 1fr; }
      .hero { grid-template-columns: 1fr 72px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1 id="room-title">AC</h1>
        <div id="status" class="status">Connecting...</div>
      </div>
      <button id="refresh" class="act" title="Refresh">↻</button>
    </header>

    <nav class="rooms" id="rooms"></nav>

    <section class="panel hero">
      <div>
        <div id="indoor" class="temp">--</div>
        <div id="summary" class="sub">Loading controller state</div>
      </div>
      <button id="power" class="power" title="Power">⏻</button>
    </section>

    <section class="panel">
      <span class="label">Target temperature</span>
      <div class="stepper">
        <button id="down">-</button>
        <div id="setpoint" class="setpoint">-- C</div>
        <button id="up">+</button>
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <span class="label">Mode</span>
        <div class="seg" id="modes">
          <button class="act" data-mode="COOL">Cool</button>
          <button class="act" data-mode="HEAT">Heat</button>
          <button class="act" data-mode="AUTO">Auto</button>
          <button class="act" data-mode="FAN">Fan</button>
        </div>
      </div>
      <div class="panel">
        <span class="label">Fan</span>
        <div class="seg" id="fans">
          <button class="act" data-fan="LOW">Low</button>
          <button class="act" data-fan="MID">Mid</button>
          <button class="act" data-fan="HIGH">High</button>
          <button class="act" data-fan="AUTO">Auto</button>
        </div>
      </div>
    </section>

    <section class="panel details">
      <div>Outdoor <span id="outdoor" class="value">--</span></div>
      <div>Filter <span id="filter" class="value">--</span></div>
      <div>Cooling setpoint <span id="cooling" class="value">--</span></div>
      <div>Heating setpoint <span id="heating" class="value">--</span></div>
    </section>
  </main>

  <script type="application/json" id="devices-json">__DEVICES_JSON__</script>
  <script>
    const DEVICES = JSON.parse(document.getElementById('devices-json').textContent);
    const els = {
      title: document.getElementById('room-title'),
      status: document.getElementById('status'),
      rooms: document.getElementById('rooms'),
      indoor: document.getElementById('indoor'),
      summary: document.getElementById('summary'),
      power: document.getElementById('power'),
      setpoint: document.getElementById('setpoint'),
      outdoor: document.getElementById('outdoor'),
      filter: document.getElementById('filter'),
      cooling: document.getElementById('cooling'),
      heating: document.getElementById('heating'),
    };

    function pickInitialRoom() {
      const hash = location.hash.replace('#', '');
      if (DEVICES.find(d => d.id === hash)) return hash;
      const stored = localStorage.getItem('daikin.room');
      if (DEVICES.find(d => d.id === stored)) return stored;
      return DEVICES[0]?.id;
    }
    let currentRoom = pickInitialRoom();
    let state = null;
    let busy = false;

    function celsius(value) {
      return value === null || value === undefined ? '--' : `${value} C`;
    }

    function activeAttr(selector, value, attr) {
      document.querySelectorAll(selector).forEach(btn => {
        btn.classList.toggle('active', btn.dataset[attr] === value);
      });
    }

    function allButtons() {
      return Array.from(document.querySelectorAll('button'));
    }

    function setBusy(next) {
      busy = next;
      allButtons().forEach(btn => btn.disabled = next);
      if (next) els.status.textContent = 'Working...';
    }

    function renderRooms() {
      els.rooms.innerHTML = '';
      DEVICES.forEach(d => {
        const btn = document.createElement('button');
        btn.textContent = d.name;
        btn.dataset.room = d.id;
        if (d.id === currentRoom) btn.classList.add('active');
        btn.onclick = () => switchRoom(d.id);
        els.rooms.appendChild(btn);
      });
      const dev = DEVICES.find(d => d.id === currentRoom);
      if (dev) els.title.textContent = dev.name;
    }

    function clearState() {
      state = null;
      els.indoor.textContent = '--';
      els.summary.textContent = 'Loading...';
      els.setpoint.textContent = '-- C';
      els.outdoor.textContent = '--';
      els.filter.textContent = '--';
      els.filter.classList.remove('warn');
      els.cooling.textContent = '--';
      els.heating.textContent = '--';
      els.power.classList.remove('on');
      activeAttr('#modes button', null, 'mode');
      activeAttr('#fans button', null, 'fan');
    }

    function render(data) {
      state = data;
      const mode = data.operation_mode?.operation_mode || '--';
      const on = !!data.power_state?.turn_on;
      const indoor = data.temperatures?.indoor;
      const outdoor = data.temperatures?.outdoor;
      const cooling = data.set_point?.cooling_set_point;
      const heating = data.set_point?.heating_set_point;
      const target = mode === 'HEAT' ? heating : cooling;
      const fan = data.fan_speed?.cooling_fan_speed || data.fan_speed?.heating_fan_speed || '--';

      els.indoor.textContent = celsius(indoor);
      els.summary.textContent = `${on ? 'On' : 'Off'} · ${mode} · Fan ${fan}`;
      els.power.classList.toggle('on', on);
      els.setpoint.textContent = celsius(target);
      els.outdoor.textContent = celsius(outdoor);
      els.filter.textContent = data.clean_filter_indicator?.clean_filter_indicator ? 'Clean filter' : 'OK';
      els.filter.classList.toggle('warn', !!data.clean_filter_indicator?.clean_filter_indicator);
      els.cooling.textContent = celsius(cooling);
      els.heating.textContent = celsius(heating);
      activeAttr('#modes button', mode, 'mode');
      activeAttr('#fans button', fan, 'fan');
      els.status.textContent = 'Connected';
    }

    function withRoom(path) {
      const sep = path.includes('?') ? '&' : '?';
      return `${path}${sep}room=${encodeURIComponent(currentRoom)}`;
    }

    async function api(path, body) {
      setBusy(true);
      const room = currentRoom;
      try {
        const res = await fetch(withRoom(path), {
          method: body ? 'POST' : 'GET',
          headers: body ? {'content-type': 'application/json'} : {},
          body: body ? JSON.stringify(body) : undefined,
        });
        const data = await res.json();
        if (room !== currentRoom) return; // user switched away mid-request
        if (!res.ok || data.ok === false) {
          const friendly = data.code === 'unreachable'
            ? 'Not reachable. Pair this controller on the Pi, then tap ↻ to retry.'
            : (data.error || res.statusText);
          throw new Error(friendly);
        }
        if (data.status) {
          render(data.status);
          if (data.verified === false) els.status.textContent = 'Unverified state. Refresh to read the controller.';
        }
        else await refresh();
      } catch (err) {
        if (room === currentRoom) els.status.textContent = err.message || 'Request failed';
      } finally {
        if (room === currentRoom) setBusy(false);
      }
    }

    async function refresh(opts) {
      const force = opts && opts.force ? '?force=1' : '';
      await api(`/api/status${force}`);
    }

    function switchRoom(id) {
      if (id === currentRoom) return;
      currentRoom = id;
      localStorage.setItem('daikin.room', id);
      history.replaceState(null, '', `#${id}`);
      renderRooms();
      clearState();
      els.status.textContent = 'Switching...';
      refresh();
    }

    document.getElementById('refresh').onclick = () => refresh({force: true});
    els.power.onclick = () => api('/api/power', {state: state?.power_state?.turn_on ? 'OFF' : 'ON'});
    document.getElementById('up').onclick = () => {
      const mode = state?.operation_mode?.operation_mode;
      const current = mode === 'HEAT' ? state?.set_point?.heating_set_point : state?.set_point?.cooling_set_point;
      api('/api/setpoint', {value: Math.min(30, (current ?? 20) + 1)});
    };
    document.getElementById('down').onclick = () => {
      const mode = state?.operation_mode?.operation_mode;
      const current = mode === 'HEAT' ? state?.set_point?.heating_set_point : state?.set_point?.cooling_set_point;
      api('/api/setpoint', {value: Math.max(16, (current ?? 20) - 1)});
    };
    document.querySelectorAll('#modes button').forEach(btn => {
      btn.onclick = () => api('/api/mode', {mode: btn.dataset.mode});
    });
    document.querySelectorAll('#fans button').forEach(btn => {
      btn.onclick = () => api('/api/fan', {speed: btn.dataset.fan});
    });

    renderRooms();
    refresh();
  </script>
</body>
</html>
"""


def remaining_time(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeviceError("expired", "command expired; send a new command if still wanted")
    return remaining


@contextmanager
def locked(lock, deadline=None):
    acquired = lock.acquire() if deadline is None else lock.acquire(timeout=remaining_time(deadline))
    if not acquired:
        raise DeviceError("expired", "command expired while waiting for Bluetooth")
    try:
        if deadline is not None:
            remaining_time(deadline)
        yield
    finally:
        lock.release()


def run_pymadoka(mac, *args, deadline=None):
    """Run the pymadoka CLI under the global BLE lock.

    Retries up to PYMADOKA_RETRIES extra times on transient BLE errors before
    surfacing the failure. A short settle delay is held inside the lock after
    every attempt so BlueZ can release the connection before the next caller.
    """
    cmd = [PYMADOKA, "-a", mac, "-d", ADAPTER, "--clean", *args]
    last_error = None
    # A timed-out write may already have reached the controller. Never replay it.
    attempts = PYMADOKA_RETRIES + 1 if args[:1] == ("get-status",) else 1
    with locked(BLE_LOCK, deadline):
        _stop_ble_discovery()
        try:
            for attempt in range(attempts):
                try:
                    timeout = PYMADOKA_TIMEOUT
                    if deadline is not None:
                        timeout = min(timeout, remaining_time(deadline))
                    proc = subprocess.run(
                        cmd, text=True, capture_output=True, timeout=timeout,
                    )
                    if proc.returncode == 0:
                        last_error = None
                        output = proc.stdout.strip()
                        if args[:1] == ("get-status",):
                            if not output:
                                last_error = RuntimeError(f"pymadoka get-status returned empty output: {proc.stderr.strip()}")
                                raise last_error
                            try:
                                data = json.loads(output)
                            except json.JSONDecodeError as exc:
                                last_error = RuntimeError(
                                    f"pymadoka get-status returned non-JSON output: {exc}"
                                )
                                raise last_error
                            if (not isinstance(data, dict)
                                    or not isinstance(data.get("power_state"), dict)
                                    or type(data["power_state"].get("turn_on")) is not bool
                                    or not isinstance(data.get("operation_mode"), dict)
                                    or not isinstance(data["operation_mode"].get("operation_mode"), str)
                                    or not isinstance(data.get("set_point"), dict)
                                    or any(type(data["set_point"].get(key)) not in (int, float)
                                           or not math.isfinite(data["set_point"][key])
                                           for key in ("cooling_set_point", "heating_set_point"))):
                                last_error = RuntimeError(
                                    "pymadoka get-status returned incomplete status"
                                )
                                raise last_error
                            return data
                        # The CLI catches some exceptions and still exits 0.
                        # Only a structured acknowledgement is a successful write.
                        if not output:
                            raise RuntimeError(f"pymadoka {args[0]} returned no acknowledgement: {proc.stderr.strip()}")
                        try:
                            data = json.loads(output)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(f"pymadoka {args[0]} returned non-JSON output") from exc
                        expected = {}
                        if args[0] == "set-power-state":
                            expected = {"turn_on": args[1] == "ON"}
                        elif args[0] == "set-operation-mode":
                            expected = {"operation_mode": args[1]}
                        elif args[0] == "set-fan-speed":
                            expected = {"cooling_fan_speed": args[1], "heating_fan_speed": args[2]}
                        elif args[0] == "set-set-point":
                            expected = {"cooling_set_point": int(args[1]), "heating_set_point": int(args[2])}
                        if not isinstance(data, dict) or not expected or any(
                                type(data.get(key)) is not type(value) or data[key] != value
                                for key, value in expected.items()):
                            raise RuntimeError(f"pymadoka {args[0]} returned an invalid acknowledgement")
                        return data
                    detail = (proc.stderr or proc.stdout or "").strip()
                    last_error = RuntimeError(
                        detail or f"pymadoka exited with {proc.returncode}"
                    )
                except subprocess.TimeoutExpired as exc:
                    # Python 3.3+ kills + waits the child before raising, so no
                    # zombie risk. We just record and (maybe) retry.
                    last_error = RuntimeError(f"pymadoka timed out after {exc.timeout}s")
                except DeviceError:
                    raise
                except RuntimeError as exc:
                    last_error = exc
                if attempt + 1 >= attempts or not _is_retryable(str(last_error)):
                    raise last_error
                time.sleep(PYMADOKA_RETRY_BACKOFF)
            # Defensive: loop must either return or raise above.
            raise last_error or RuntimeError("pymadoka failed without diagnostic")
        finally:
            if last_error is not None and _is_retryable(str(last_error)):
                _cleanup_ble_device(mac)
            _stop_ble_discovery()
            if POST_CALL_SETTLE > 0:
                time.sleep(POST_CALL_SETTLE)


def _cleanup_ble_device(mac):
    """Best-effort cleanup after a failed BlueZ/Madoka call.

    `pymadoka` is killed on timeout, but BlueZ can briefly keep connection
    state around. A short disconnect is harmless when the device is already
    disconnected and helps the next queued HomeKit write start cleanly.
    """
    try:
        subprocess.run(
            ["bluetoothctl", "disconnect", mac],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        print(f"BLE cleanup failed for {mac}: {exc}", flush=True)


def _stop_ble_discovery():
    """Ensure an accidental/manual BLE scan does not contend with control calls."""
    try:
        subprocess.run(
            ["bluetoothctl", "scan", "off"],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        print(f"BLE scan-off failed: {exc}", flush=True)


class DeviceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def check_cooldown(device):
    with STATE_LOCK:
        fail = FAILURES.get(device["id"])
    if fail and (time.time() - fail["ts"] < FAILURE_COOLDOWN):
        raise DeviceError(fail["code"], fail["message"])


def record_failure(device, exc):
    code = classify_error(str(exc))
    with STATE_LOCK:
        FAILURES[device["id"]] = {"ts": time.time(), "code": code, "message": str(exc)}
    return DeviceError(code, str(exc))


def clear_failure(device):
    with STATE_LOCK:
        FAILURES.pop(device["id"], None)


# Per-device serialization for the full prefetch+write+cache-patch sequence in
# handle_action. The global BLE_LOCK only serializes pymadoka subprocesses; this
# lock keeps two concurrent writes to the SAME room from landing their cache
# patches out of order (write A's BLE finishes first then write B's BLE finishes,
# then A's cache patch overwrites B's — which would diverge from reality).
_DEVICE_LOCKS = {}
_DEVICE_LOCKS_GUARD = threading.Lock()


def _device_lock(device):
    with _DEVICE_LOCKS_GUARD:
        lock = _DEVICE_LOCKS.get(device["id"])
        if lock is None:
            lock = threading.RLock()
            _DEVICE_LOCKS[device["id"]] = lock
    return lock


def _cache_get(device):
    """Return (data, verified) tuple if cache is fresh, else None.

    `verified=True` means the data came from a real BLE get-status call.
    `verified=False` means it was synthesized from a write patch — we believe
    the AC accepted the write but we never read it back, so don't short-circuit
    further writes against this entry.
    """
    with STATE_LOCK:
        cached = STATUS_CACHE.get(device["id"])
    if not cached or (time.time() - cached["ts"] >= STATUS_TTL):
        return None
    return cached["data"], cached.get("verified", True)


def _cache_get_any(device):
    """Return cached state even after TTL expiry, marking stale entries unverified.

    User writes should not need a fresh BLE read just to patch local state. Using
    stale cache as a merge base is safer than letting a failed background poll
    block a power/mode/fan command.
    """
    with STATE_LOCK:
        cached = STATUS_CACHE.get(device["id"])
    if not cached:
        return None
    return cached["data"], False


def _status_for_write(device):
    entry = _cache_get(device)
    if entry is not None:
        return entry
    stale = _cache_get_any(device)
    if stale is not None:
        return stale
    return {}, False


def _cache_set(device, data, verified=True):
    with STATE_LOCK:
        STATUS_CACHE[device["id"]] = {
            "data": data, "ts": time.time(), "verified": verified,
        }


def invalidate(device):
    with STATE_LOCK:
        STATUS_CACHE.pop(device["id"], None)


def get_status_full(device, force=False, bypass_cooldown=False, deadline=None):
    # Reads must share the write lock: an older read must not overwrite a newer
    # write's cache patch after releasing the global BLE lock.
    with locked(_device_lock(device), deadline):
        return _get_status_full(device, force, bypass_cooldown, deadline)


def _get_status_full(device, force, bypass_cooldown, deadline):
    """Return (data, verified) where verified is True iff the data came from
    a real BLE read (vs a post-write synthesized cache entry)."""
    if not force:
        entry = _cache_get(device)
        if entry is not None:
            return entry
    if not bypass_cooldown:
        check_cooldown(device)
    try:
        data = run_pymadoka(device["mac"], "get-status", deadline=deadline)
    except DeviceError:
        raise
    except Exception as exc:
        err = record_failure(device, exc)
        print(f'[{device["id"]}] status failed ({err.code}): {err}', flush=True)
        raise err
    clear_failure(device)
    _cache_set(device, data, verified=True)
    return data, True


def get_status(device, force=False):
    return get_status_full(device, force=force)[0]


def ble_write(device, *args, bypass_cooldown=False, deadline=None):
    """Perform a single Madoka write. The caller is responsible for updating
    the cache afterwards (see handle_action) so we don't pay a second BLE
    round-trip just to read back what we just wrote."""
    if not bypass_cooldown:
        check_cooldown(device)
    try:
        run_pymadoka(device["mac"], *args, deadline=deadline)
    except DeviceError:
        raise
    except Exception as exc:
        invalidate(device)
        raise record_failure(device, exc)
    clear_failure(device)


def resolve_device(room_id):
    if not room_id:
        raise ValueError("missing room")
    dev = DEVICE_BY_ID.get(room_id)
    if not dev:
        raise ValueError(f"unknown room: {room_id}")
    return dev


def handle_action(device, payload, deadline=None):
    """Apply a single Madoka write and return the synthesized post-write state.

    Previously this did a forced get-status BEFORE and AFTER each write — three
    BLE round-trips per HomeKit operation, often blowing past HomeKit timeouts.
    We now use only cached state for simple writes, patch the cache locally with
    the value we just wrote, and bypass background-poll cooldowns for user
    commands. A failed poll should not stop someone turning an AC off.

    A per-device lock wraps the prefetch+write+patch sequence so two concurrent
    writes to the same room can't land their cache patches out of order.
    """
    action = payload.get("action")
    if deadline is None:
        deadline = time.monotonic() + WRITE_TIMEOUT
    with locked(_device_lock(device), deadline):
        # Never do a fresh BLE pre-read for simple user writes. If cache is
        # stale/missing, write anyway and use the cache only as a merge base.
        status, _ = _status_for_write(device)

        def commit(write_args, patch):
            """Run the BLE write, then patch the room's serialized cache."""
            print(f'[{device["id"]}] command start: {" ".join(write_args)}', flush=True)
            try:
                ble_write(device, *write_args, bypass_cooldown=True, deadline=deadline)
            except DeviceError as exc:
                print(f'[{device["id"]}] command failed ({exc.code}): {exc}', flush=True)
                raise
            print(f'[{device["id"]}] command completed: {" ".join(write_args)}', flush=True)
            with STATE_LOCK:
                cached = STATUS_CACHE.get(device["id"])
                base = copy.deepcopy(cached["data"]) if cached else copy.deepcopy(status)
                patch(base)
                # Mark as unverified — we patched locally without reading back.
                STATUS_CACHE[device["id"]] = {
                    "data": base, "ts": time.time(), "verified": False,
                }
            return base

        if action == "power":
            value = payload.get("state")
            if value not in {"ON", "OFF"}:
                raise ValueError("state must be ON or OFF")
            # Even a recent read can predate a physical-remote change. In
            # particular, never skip an explicit OFF based on cached state.

            def patch(s):
                s.setdefault("power_state", {})["turn_on"] = (value == "ON")
            return commit(("set-power-state", value), patch)

        if action == "mode":
            value = payload.get("mode")
            if value not in {"FAN", "DRY", "AUTO", "COOL", "HEAT", "VENTILATION"}:
                raise ValueError("invalid mode")
            def patch(s):
                s.setdefault("operation_mode", {})["operation_mode"] = value
            return commit(("set-operation-mode", value), patch)

        if action == "fan":
            value = payload.get("speed")
            if value not in {"LOW", "MID", "HIGH", "AUTO"}:
                raise ValueError("invalid fan speed")
            def patch(s):
                f = s.setdefault("fan_speed", {})
                f["cooling_fan_speed"] = value
                f["heating_fan_speed"] = value
            return commit(("set-fan-speed", value, value), patch)

        if action == "setpoint":
            # pymadoka's set-set-point takes BOTH cooling and heating, so a
            # partial update needs a live value for the side not being changed.
            # Supplying both sides avoids that extra round-trip.
            overrides = {}
            for side in ("cooling", "heating", "value"):
                if payload.get(side) is not None:
                    overrides[side] = parse_setpoint(payload[side], side)
            if not overrides:
                raise ValueError("a cooling, heating, or value setpoint is required")
            if "value" in overrides and len(overrides) != 1:
                raise ValueError("value cannot be combined with cooling or heating")
            # Partial writes preserve the other threshold from a live read,
            # never from an old or optimistically patched cache.
            if "value" in overrides or len(overrides) < 2:
                status, _ = get_status_full(
                    device,
                    force=True,
                    bypass_cooldown=True,
                    deadline=deadline,
                )
            if "value" in overrides:
                side = "heating" if status["operation_mode"]["operation_mode"] == "HEAT" else "cooling"
                overrides[side] = overrides.pop("value")
            set_point = status.get("set_point", {})
            cooling = parse_setpoint(overrides.get("cooling", set_point.get("cooling_set_point")), "cooling")
            heating = parse_setpoint(overrides.get("heating", set_point.get("heating_set_point")), "heating")

            new_cooling, new_heating = cooling, heating

            def patch(s):
                sp = s.setdefault("set_point", {})
                sp["cooling_set_point"] = new_cooling
                sp["heating_set_point"] = new_heating
            return commit(("set-set-point", str(cooling), str(heating)), patch)

        raise ValueError("unknown action")


def render_index():
    payload = json.dumps([{"id": d["id"], "name": d["name"]} for d in DEVICES])
    # Embedded in <script type="application/json">; neutralize only the script-closer.
    safe = payload.replace("</", "<\\/")
    return HTML.replace("__DEVICES_JSON__", safe)


class DaikinHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = REQUEST_QUEUE_SIZE


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.request.settimeout(REQUEST_TIMEOUT)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Client (typically HomeBridge plugin) timed out and closed the socket
            # before we finished. The operation already happened — just stop trying
            # to write to a dead socket so we don't log a huge traceback.
            pass

    def _parsed(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        return parsed.path, query

    def _room(self, query, fallback_payload=None):
        room_id = (query.get("room") or [None])[0]
        if not room_id and fallback_payload:
            room_id = fallback_payload.get("room")
        if not room_id:
            # Be explicit instead of silently defaulting to DEVICES[0], which
            # used to mean a malformed request could control the wrong AC.
            raise ValueError("missing 'room' query parameter or payload field")
        return resolve_device(room_id)

    def do_GET(self):
        try:
            path, query = self._parsed()
            if path in {"/", "/index.html"}:
                body = render_index().encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif path == "/healthz":
                with STATE_LOCK:
                    diagnostics = {}
                    for device in DEVICES:
                        cached = STATUS_CACHE.get(device["id"])
                        failure = FAILURES.get(device["id"])
                        age = max(0, time.time() - cached["ts"]) if cached else None
                        diagnostics[device["id"]] = {
                            "age_seconds": age,
                            "verified": bool(cached and cached.get("verified", True)
                                             and age < STATUS_TTL and not failure),
                            "last_error": failure["code"] if failure else None,
                        }
                self.send_json({"ok": True, "rooms": len(DEVICES), "devices": diagnostics})
            elif path == "/api/devices":
                self.send_json({"ok": True, "devices": [
                    {"id": d["id"], "name": d["name"], "mac": d["mac"]} for d in DEVICES
                ]})
            elif path == "/api/status":
                device = self._room(query)
                force = (query.get("force") or ["0"])[0] in {"1", "true", "yes"}
                with _device_lock(device):
                    status, verified = get_status_full(device, force=force, bypass_cooldown=force)
                    with STATE_LOCK:
                        cached = STATUS_CACHE.get(device["id"])
                        age = max(0, time.time() - cached["ts"]) if cached else 0
                payload = {"ok": True, "room": device["id"], "status": status,
                           "verified": verified, "age_seconds": age}
                self.send_json(payload)
            else:
                self.send_error(404)
        except DeviceError as exc:
            self.send_json({"ok": False, "code": exc.code, "error": exc.message}, 503)
        except (ValueError, KeyError) as exc:
            # Client-side issues — missing/invalid params, bad room id, etc.
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self):
        try:
            deadline = time.monotonic() + WRITE_TIMEOUT
            expires = self.headers.get("X-Daikin-Expires-At")
            if expires is not None:
                expires = float(expires)
                if not math.isfinite(expires):
                    raise ValueError("invalid command expiry")
                deadline = min(deadline, time.monotonic() + expires / 1000 - time.time())
            remaining_time(deadline)
            path, query = self._parsed()
            try:
                length = int(self.headers.get("content-length", "0") or "0")
            except ValueError:
                self.send_json({"ok": False, "error": "invalid content-length"}, 400)
                return
            if length < 0:
                self.send_json({"ok": False, "error": "invalid content-length"}, 400)
                return
            if length > MAX_POST_BODY:
                self.send_json(
                    {"ok": False, "error": f"body too large (>{MAX_POST_BODY} bytes)"},
                    413,
                )
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": f"invalid JSON: {exc}"}, 400)
                return
            if not isinstance(payload, dict):
                self.send_json({"ok": False, "error": "request body must be a JSON object"}, 400)
                return
            mapping = {
                "/api/power": "power",
                "/api/mode": "mode",
                "/api/fan": "fan",
                "/api/setpoint": "setpoint",
            }
            action = mapping.get(path)
            if not action:
                self.send_error(404)
                return
            device = self._room(query, payload)
            payload["action"] = action
            self.send_json({"ok": True, "room": device["id"],
                            "status": handle_action(device, payload, deadline),
                            "verified": False})
        except DeviceError as exc:
            self.send_json({"ok": False, "code": exc.code, "error": exc.message}, 503)
        except (ValueError, KeyError) as exc:
            # Validation errors raised from _room or handle_action — these are
            # client bugs, not server bugs. 4xx so HomeBridge surfaces them
            # without retrying as if BLE were transiently broken.
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)


def main():
    if not Path(PYMADOKA).exists():
        raise SystemExit(f"pymadoka not found: {PYMADOKA}")
    if not DEVICES:
        raise SystemExit("no devices configured")
    server = DaikinHTTPServer((HOST, PORT), Handler)
    rooms = ", ".join(f"{d['name']} ({d['mac']})" for d in DEVICES)
    print(f"Listening on http://{HOST}:{PORT} for rooms: {rooms}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
