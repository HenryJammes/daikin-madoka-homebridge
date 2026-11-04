"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const http = require("node:http");
const { EventEmitter, once } = require("node:events");
const register = require("../index");

const status = () => ({
  power_state: { turn_on: false },
  operation_mode: { operation_mode: "COOL" },
  set_point: { cooling_set_point: 31, heating_set_point: 20 },
  fan_speed: { cooling_fan_speed: "AUTO", heating_fan_speed: "AUTO" },
  temperatures: { indoor: 18.5 },
});

class Service {
  constructor() { this.characteristics = new Map(); }
  getCharacteristic(key) {
    if (!this.characteristics.has(key)) {
      this.characteristics.set(key, {
        setProps(props) { this.props = props; return this; },
        updateValue(value) { this.value = value; return this; },
        onGet(fn) { this.get = fn; return this; },
        onSet(fn) { this.set = fn; return this; },
      });
    }
    return this.characteristics.get(key);
  }
  setCharacteristic(key, value) { this.getCharacteristic(key).updateValue(value); return this; }
  updateCharacteristic(key, value) { return this.setCharacteristic(key, value); }
}

class Accessory {
  constructor(name, uuid) {
    this.UUID = uuid;
    this.displayName = name;
    this.context = {};
    this.services = new Map([["information", new Service()]]);
  }
  getService(type) { return this.services.get(type); }
  addService(type) { const svc = new Service(); this.services.set(type, svc); return svc; }
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

async function fixture(t, respond) {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const request = { method: req.method, url: req.url, body: raw ? JSON.parse(raw) : null, headers: req.headers };
    requests.push(request);
    const result = respond ? await respond(request) : null;
    if (result?.disconnect) {
      req.socket.destroy();
      return;
    }
    res.writeHead(result?.code || 200, { "content-type": "application/json" });
    res.end(JSON.stringify(result?.body || { ok: true, verified: true, status: status() }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  let Platform;
  const api = new EventEmitter();
  const Characteristic = {
    Active: { ACTIVE: 1, INACTIVE: 0 },
    CurrentHeaterCoolerState: { INACTIVE: 0, IDLE: 1, HEATING: 2, COOLING: 3 },
    TargetHeaterCoolerState: { AUTO: 0, HEAT: 1, COOL: 2 },
    TemperatureDisplayUnits: { CELSIUS: 0 },
  };
  for (const name of ["Manufacturer", "Model", "SerialNumber", "FirmwareRevision", "Name",
    "CurrentTemperature", "CoolingThresholdTemperature", "HeatingThresholdTemperature", "RotationSpeed"]) {
    Characteristic[name] = name;
  }
  api.hap = {
    Service: { AccessoryInformation: "information", HeaterCooler: "climate" },
    Characteristic,
    uuid: { generate: (value) => value },
    HapStatusError: class extends Error {},
    HAPStatus: { SERVICE_COMMUNICATION_FAILURE: -70402, INVALID_VALUE_IN_REQUEST: -70410 },
  };
  api.platformAccessory = Accessory;
  for (const name of ["registerPlatformAccessories", "updatePlatformAccessories", "unregisterPlatformAccessories"]) {
    api[name] = () => {};
  }
  api.registerPlatform = (_plugin, _platform, impl) => { Platform = impl; };
  register(api);
  const warnings = [];
  const log = { info() {}, debug() {}, warn: (line) => warnings.push(line) };
  const platform = new Platform(log, {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    devices: [{ id: "test_room", name: "Test room" }],
  }, api);
  platform.discoverDevices();
  const instance = [...platform.accessories.values()][0]._daikinInstance;
  clearTimeout(instance.pollTimer);
  instance.state = status();
  instance.lastFetch = Date.now();
  t.after(async () => {
    api.emit("shutdown");
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  return { platform, instance, requests, warnings, Characteristic, api };
}

function startWriter(instance) {
  clearTimeout(instance.writerTimer);
  return instance.runWriter();
}

test("reports the actual indoor temperature, and displays a 31 C cooling setpoint", async (t) => {
  const { instance, Characteristic } = await fixture(t);
  instance.pushAll();
  assert.equal(instance.service.getCharacteristic(Characteristic.CurrentTemperature).get(), 18.5);
  const cooling = instance.service.getCharacteristic(Characteristic.CoolingThresholdTemperature);
  assert.equal(cooling.value, 31);
  assert.equal(cooling.props.maxValue, 32);
  instance.state.temperatures.indoor = null;
  assert.throws(() => instance.displayTempFromState(instance.state));
});

test("unknown and stale state surfaces a HomeKit communication error", async (t) => {
  const { instance, Characteristic } = await fixture(t);
  instance.lastFetch = Date.now() - 301_000;
  assert.throws(() => instance.service.getCharacteristic(Characteristic.Active).get());
  instance.pushAll();
  assert.ok(instance.service.getCharacteristic(Characteristic.Active).value instanceof Error);
  instance.state = null;
  assert.throws(() => instance.readState());
});

test("pushes a fault at the five-minute boundary without waiting for another poll", async (t) => {
  const { instance, Characteristic } = await fixture(t);
  const originalNow = Date.now;
  const originalTimeout = global.setTimeout;
  let now = Date.now();
  let timer;
  try {
    Date.now = () => now;
    global.setTimeout = (fn, delay) => { timer = { fn, delay }; };
    instance.lastFetch = now;
    instance.pushAll();
    instance.scheduleExpiry();
    assert.equal(timer.delay, 300_000);
    now += 299_999;
    assert.equal(instance.readState().power_state.turn_on, false);
    now += 1;
    timer.fn();
    assert.ok(instance.service.getCharacteristic(Characteristic.Active).value instanceof Error);
  } finally {
    Date.now = originalNow;
    global.setTimeout = originalTimeout;
  }
});

test("setpoints above the CLI limit are rejected rather than silently clamped", async (t) => {
  const { instance, Characteristic } = await fixture(t);
  assert.throws(() => instance.service.getCharacteristic(Characteristic.CoolingThresholdTemperature).set(31));
  assert.equal(instance.pendingWrites.size, 0);
});

test("a newer OFF supersedes an ON waiting behind a poll", async (t) => {
  const { platform, instance, requests } = await fixture(t);
  const blocker = deferred();
  const poll = platform.enqueue("other_room", () => blocker.promise, { kind: "poll" });
  instance.writeAsync("/api/power", { state: "ON" });
  const writer = startWriter(instance);
  instance.writeAsync("/api/power", { state: "OFF" });
  blocker.resolve();
  await Promise.all([poll, writer]);
  const writes = requests.filter((r) => r.method === "POST");
  assert.deepEqual(writes.map((r) => r.body), [{ state: "OFF" }]);
  assert.ok(Number(writes[0].headers["x-daikin-expires-at"]) > Date.now());
});

test("failed in-flight ON is not retried ahead of newer OFF", async (t) => {
  const started = deferred();
  const finish = deferred();
  const { instance, requests, warnings } = await fixture(t, async (request) => {
    if (request.body?.state === "ON") {
      started.resolve();
      await finish.promise;
      return { code: 503, body: { ok: false, error: "timeout" } };
    }
  });
  instance.writeAsync("/api/power", { state: "ON" });
  const writer = startWriter(instance);
  await started.promise;
  instance.writeAsync("/api/power", { state: "OFF" });
  finish.resolve();
  await writer;
  assert.deepEqual(requests.filter((r) => r.method === "POST").map((r) => r.body.state), ["ON", "OFF"]);
  assert.ok(warnings.some((line) => line.includes("not replaying")));
});

test("expired queued commands are logged and never posted", async (t) => {
  const { instance, requests, warnings } = await fixture(t);
  instance.writeAsync("/api/power", { state: "ON" });
  instance.pendingWrites.get("/api/power").expiresAt = Date.now() - 1;
  await startWriter(instance);
  assert.equal(requests.filter((r) => r.method === "POST").length, 0);
  assert.ok(warnings.some((line) => line.includes("expired")));
});

test("coalesces paired thresholds without inventing the untouched threshold", async (t) => {
  const { instance, requests, Characteristic } = await fixture(t);
  instance.service.getCharacteristic(Characteristic.HeatingThresholdTemperature).set(22);
  assert.deepEqual(instance.pendingWrites.get("/api/setpoint").body, { heating: 22 });
  instance.service.getCharacteristic(Characteristic.CoolingThresholdTemperature).set(28);
  await startWriter(instance);
  assert.deepEqual(requests.find((r) => r.method === "POST").body, { heating: 22, cooling: 28 });
});

test("pending intent remains separate from an older acknowledged state", async (t) => {
  const { instance } = await fixture(t);
  instance.writeAsync("/api/power", { state: "ON" });
  assert.equal(instance.state.power_state.turn_on, false);
  assert.equal(instance.readState().power_state.turn_on, true);
  instance.writeAsync("/api/power", { state: "OFF" });
  instance.state.power_state.turn_on = true;
  assert.equal(instance.readState().power_state.turn_on, false);
});

test("status failures cannot refresh the age of stale data", async (t) => {
  const { instance, warnings } = await fixture(t, () => ({
    code: 200, body: { ok: true, verified: false, status: status() },
  }));
  const previous = Date.now() - 301_000;
  instance.lastFetch = previous;
  await instance.refresh(true);
  assert.equal(instance.lastFetch, previous);
  assert.equal(instance.consecutiveUnreachable, 1);
  assert.throws(() => instance.readState());
  assert.equal(warnings.length, 1);
});

test("transport failures are counted and logged like backend failures", async (t) => {
  const { instance, warnings } = await fixture(t, () => ({ disconnect: true }));
  instance.lastFetch = Date.now() - 301_000;
  await instance.refresh(true);
  assert.equal(instance.consecutiveUnreachable, 1);
  assert.equal(warnings.length, 1);
  assert.throws(() => instance.readState());
});

test("failed write and failed refresh cannot leave optimistic ON as truth", async (t) => {
  const { instance, requests } = await fixture(t, () => ({
    code: 503, body: { ok: false, error: "timeout" },
  }));
  instance.writeAsync("/api/power", { state: "ON" });
  await startWriter(instance);
  assert.equal(instance.state.power_state.turn_on, false);
  assert.equal(instance.desiredWrites.size, 0);
  assert.equal(requests.filter((r) => r.method === "POST").length, 1);
  assert.throws(() => instance.readState());
});

test("successful cache reads retain the backend sample age", async (t) => {
  const { instance } = await fixture(t, () => ({
    body: { ok: true, verified: true, age_seconds: 45, status: status() },
  }));
  await instance.refresh(true);
  assert.ok(Date.now() - instance.lastFetch >= 45_000);
});

test("write queue is bounded and OFF has priority", async (t) => {
  const { platform } = await fixture(t);
  platform.maxQueueDepth = 2;
  const blocker = deferred();
  const active = platform.enqueue("other", () => blocker.promise, { kind: "poll" });
  const order = [];
  const normal = platform.enqueue("room", () => order.push("normal"));
  const off = platform.enqueue("room", () => order.push("off"), { priority: 20 });
  await assert.rejects(platform.enqueue("room", () => {}), /queue is full/);
  blocker.resolve();
  await Promise.all([active, normal, off]);
  assert.deepEqual(order, ["off", "normal"]);
});

test("shutdown discards unsent commands", async (t) => {
  const { instance, requests, api } = await fixture(t);
  instance.writeAsync("/api/power", { state: "ON" });
  api.emit("shutdown");
  await instance.runWriter();
  assert.equal(requests.length, 0);
});
