"use strict";

const http = require("http");
const { version } = require("./package.json");

const PLUGIN_NAME = "homebridge-daikin-web";
const PLATFORM_NAME = "DaikinWeb";
const WRITE_TIMEOUT_MS = 60_000;
const MAX_STATE_AGE_MS = 5 * 60_000;

module.exports = (api) => {
  api.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, DaikinWebPlatform);
};

class DaikinWebPlatform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    this.api = api;
    this.accessories = new Map();
    this.baseUrl = (this.config.baseUrl || "http://127.0.0.1:5050").replace(/\/$/, "");
    this.devices = Array.isArray(this.config.devices) ? this.config.devices : [];
    this.pollIntervalSec = Number(this.config.pollIntervalSec) || 60;
    // One BLE adapter means one backend call at a time. Queueing here prevents
    // HTTP requests from timing out while waiting behind another room's BLE op,
    // and lets user writes jump ahead of background polls.
    this.queue = [];
    this.queueRunning = false;
    this.activeJobKind = null;
    this.queueSeq = 0;
    this.maxQueueDepth = Number(this.config.maxQueueDepth) || 24;

    this.api.on("didFinishLaunching", () => {
      this.log.info(`Daikin Web platform booted, base URL = ${this.baseUrl}, devices = ${this.devices.length}`);
      this.discoverDevices();
    });
    this.api.on("shutdown", () => {
      for (const accessory of this.accessories.values()) accessory._daikinInstance?.cleanup();
    });
  }

  hasQueuedWrite() {
    return this.activeJobKind === "write" || this.queue.some((job) => job.kind === "write");
  }

  enqueue(deviceId, fn, opts = {}) {
    const kind = opts.kind || "write";
    const priority = opts.priority ?? (kind === "write" ? 10 : 0);
    const dedupeKey = opts.dedupeKey;

    if (dedupeKey) {
      const existing = this.queue.find((job) => job.dedupeKey === dedupeKey);
      if (existing) return existing.promise;
    }

    while (this.queue.length >= this.maxQueueDepth) {
      const pollIndex = this.queue.findIndex((job) => job.kind === "poll");
      if (pollIndex === -1) break;
      const [evicted] = this.queue.splice(pollIndex, 1);
      evicted.resolve(evicted.skipValue ? evicted.skipValue() : undefined);
    }

    if (kind === "poll" && this.queue.length >= this.maxQueueDepth) {
      return Promise.resolve(opts.skipValue ? opts.skipValue() : undefined);
    }
    if (this.queue.length >= this.maxQueueDepth) {
      return Promise.reject(new Error("command queue is full; send a new command later"));
    }

    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    this.queue.push({
      deviceId,
      fn,
      kind,
      priority,
      dedupeKey,
      skipIf: opts.skipIf,
      skipValue: opts.skipValue,
      seq: this.queueSeq++,
      resolve,
      reject,
      promise,
    });
    this.drainQueue();
    return promise;
  }

  async drainQueue() {
    if (this.queueRunning) return;
    this.queueRunning = true;
    try {
      while (this.queue.length > 0) {
        this.queue.sort((a, b) => (b.priority - a.priority) || (a.seq - b.seq));
        const job = this.queue.shift();
        try {
          this.activeJobKind = job.kind;
          if (job.skipIf && job.skipIf()) {
            job.resolve(job.skipValue ? job.skipValue() : undefined);
          } else {
            job.resolve(await job.fn());
          }
        } catch (err) {
          job.reject(err);
        } finally {
          this.activeJobKind = null;
        }
      }
    } finally {
      this.queueRunning = false;
      if (this.queue.length > 0) this.drainQueue();
    }
  }

  // Called by HomeBridge once per cached accessory at startup.
  configureAccessory(accessory) {
    this.accessories.set(accessory.UUID, accessory);
  }

  discoverDevices() {
    const wanted = new Set();
    let index = 0;
    for (const dev of this.devices) {
      if (!dev || !dev.id || !dev.name) {
        this.log.warn(`Skipping device without id/name: ${JSON.stringify(dev)}`);
        continue;
      }
      const uuid = this.api.hap.uuid.generate(`daikin-web:${dev.id}`);
      wanted.add(uuid);
      let accessory = this.accessories.get(uuid);
      const startupOffsetMs = 5_000 + index * 15_000;
      if (!accessory) {
        accessory = new this.api.platformAccessory(dev.name, uuid);
        accessory.context.device = dev;
        accessory._daikinInstance = new DaikinAccessory(this, accessory, startupOffsetMs);
        this.api.registerPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, [accessory]);
        this.accessories.set(uuid, accessory);
        this.log.info(`Added new HomeKit accessory: ${dev.name}`);
      } else {
        // discoverDevices() is normally only called once per HomeBridge process
        // lifetime, but defensively tear down any existing instance so we never
        // run two poll timers / write pumps on the same accessory.
        if (accessory._daikinInstance) accessory._daikinInstance.cleanup();
        accessory.displayName = dev.name;
        accessory.context.device = dev;
        accessory._daikinInstance = new DaikinAccessory(this, accessory, startupOffsetMs);
        this.api.updatePlatformAccessories([accessory]);
        this.log.info(`Restored cached accessory: ${dev.name}`);
      }
      index += 1;
    }
    for (const [uuid, accessory] of this.accessories.entries()) {
      if (!wanted.has(uuid)) {
        if (accessory._daikinInstance) accessory._daikinInstance.cleanup();
        this.api.unregisterPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, [accessory]);
        this.accessories.delete(uuid);
        this.log.info(`Removed stale accessory: ${accessory.displayName}`);
      }
    }
  }
}

class DaikinAccessory {
  constructor(platform, accessory, startupOffsetMs = 5000) {
    this.platform = platform;
    this.accessory = accessory;
    this.device = accessory.context.device;
    this.log = platform.log;
    const { Service, Characteristic } = platform.api.hap;
    this.HAP = platform.api.hap;

    this.state = null;
    this.lastFetch = 0;
    this.fetching = null;
    this.fault = null;
    // Set by cleanup() so the poll timer's tail tick + the write pump both
    // stop scheduling more work after the accessory is removed.
    this.destroyed = false;
    // After 4 consecutive unreachable polls we back off to a slow tick so the
    // BLE radio stays free for paired rooms. The backend already enforces a
    // 2 min cooldown; this just stops us pinging it pointlessly.
    this.consecutiveUnreachable = 0;
    // Coalescing write buffer: HomeKit fires several characteristic writes
    // per single user gesture (drag the fan dial → 6 fan writes + 6 implicit
    // Active=ACTIVE writes; toggle power → 2-3 writes). Each BLE round-trip
    // takes 20-40 s, so without coalescing the queue grows faster than we
    // can drain it. We keep only the LATEST pending body per endpoint and
    // a single pump task drains them one at a time.
    this.pendingWrites = new Map();
    this.desiredWrites = new Map();
    this.writerActive = false;

    accessory.getService(Service.AccessoryInformation)
      .setCharacteristic(Characteristic.Manufacturer, "Daikin")
      .setCharacteristic(Characteristic.Model, "Madoka BRC1H (via daikin-web)")
      .setCharacteristic(Characteristic.SerialNumber, this.device.mac || this.device.id)
      .setCharacteristic(Characteristic.FirmwareRevision, version);

    this.service =
      accessory.getService(Service.HeaterCooler) ||
      accessory.addService(Service.HeaterCooler, this.device.name);

    this.service.setCharacteristic(Characteristic.Name, this.device.name);

    // All onGet handlers are cache-only and must return in <1s. Fresh values
    // are pushed via updateCharacteristic from the polling loop / writes.
    // setProps + updateValue ordering: bump bounds first, then push a default
    // that's inside the new range to silence "illegal value" warnings.

    // Active
    this.service.getCharacteristic(Characteristic.Active)
      .onGet(() => this.activeFromState(this.readState()))
      .onSet((value) => {
        const on = value === Characteristic.Active.ACTIVE;
        this.writeAsync("/api/power", { state: on ? "ON" : "OFF" });
      });

    // CurrentHeaterCoolerState
    this.service.getCharacteristic(Characteristic.CurrentHeaterCoolerState)
      .onGet(() => this.currentStateFromState(this.readState()));

    // TargetHeaterCoolerState — AUTO / HEAT / COOL (HomeKit doesn't model FAN/DRY)
    this.service.getCharacteristic(Characteristic.TargetHeaterCoolerState)
      .setProps({
        validValues: [
          Characteristic.TargetHeaterCoolerState.AUTO,
          Characteristic.TargetHeaterCoolerState.HEAT,
          Characteristic.TargetHeaterCoolerState.COOL,
        ],
      })
      .onGet(() => this.targetStateFromState(this.readState()))
      .onSet((value) => {
        let mode = "AUTO";
        if (value === Characteristic.TargetHeaterCoolerState.HEAT) mode = "HEAT";
        else if (value === Characteristic.TargetHeaterCoolerState.COOL) mode = "COOL";
        this.writeAsync("/api/mode", { mode });
      });

    // Report the real sensor, not the target: HomeKit automations consume this.
    this.service.getCharacteristic(Characteristic.CurrentTemperature)
      .setProps({ minValue: -50, maxValue: 100, minStep: 0.1 })
      .updateValue(20)
      .onGet(() => this.displayTempFromState(this.readState()));

    // Display physical-controller values up to 32 C, but reject writes above
    // the CLI's 30 C cap instead of silently clamping them.
    this.service.getCharacteristic(Characteristic.CoolingThresholdTemperature)
      .setProps({ minValue: 16, maxValue: 32, minStep: 1 })
      .updateValue(24)
      .onGet(() => this.readState().set_point?.cooling_set_point ?? 24)
      .onSet((value) => {
        const v = Math.round(value);
        this.writeAsync("/api/setpoint", { cooling: v });
      });

    // HeatingThresholdTemperature
    this.service.getCharacteristic(Characteristic.HeatingThresholdTemperature)
      .setProps({ minValue: 16, maxValue: 31, minStep: 1 })
      .updateValue(20)
      .onGet(() => this.readState().set_point?.heating_set_point ?? 20)
      .onSet((value) => {
        const v = Math.round(value);
        this.writeAsync("/api/setpoint", { heating: v });
      });

    this.service.getCharacteristic(Characteristic.TemperatureDisplayUnits)
      .updateValue(Characteristic.TemperatureDisplayUnits.CELSIUS);

    // Fan speed — LOW(25) / MID(50) / HIGH(75) / AUTO(100). The Madoka has
    // 4 discrete fan modes; the HeaterCooler service only exposes a continuous
    // slider, so we snap with minStep=25. minValue is 25 (not 0) because in
    // HomeKit RotationSpeed=0 is interpreted as "fan off" and iOS will
    // simultaneously send Active=INACTIVE, accidentally powering the AC off.
    this.service.getCharacteristic(Characteristic.RotationSpeed)
      .setProps({ minValue: 25, maxValue: 100, minStep: 25 })
      .updateValue(100)
      .onGet(() => {
        const s = this.readState();
        const fan = s.fan_speed?.cooling_fan_speed || s.fan_speed?.heating_fan_speed;
        return ({ LOW: 25, MID: 50, HIGH: 75, AUTO: 100 })[fan] ?? 100;
      })
      .onSet((value) => {
        let speed = "AUTO";
        if (value <= 25) speed = "LOW";
        else if (value <= 50) speed = "MID";
        else if (value <= 75) speed = "HIGH";
        this.writeAsync("/api/fan", { speed });
      });

    // Proactive polling so physical-remote / web-UI changes propagate to Home.
    // Rooms are staggered (startupOffsetMs grows per room) and jittered so
    // they don't all hammer the one BLE adapter at once.
    const periodMs = Math.max(20, platform.pollIntervalSec) * 1000;
    const tick = () => {
      if (this.destroyed) return;
      // Yield the BLE radio to user-initiated writes. If a write is queued
      // or actively running, push the poll out a short window so HomeKit
      // commands don't sit behind background refreshes on the single adapter.
      if (this.writerActive || this.pendingWrites.size > 0 || this.platform.hasQueuedWrite()) {
        this.pollTimer = setTimeout(tick, 8000 + Math.floor(Math.random() * 4000));
        return;
      }
      const slowed = this.consecutiveUnreachable >= 4;
      const next = (slowed ? Math.max(periodMs, 5 * 60 * 1000) : periodMs)
                   + Math.floor(Math.random() * 5000);
      this.refresh().finally(() => {
        if (this.destroyed) return;
        this.pollTimer = setTimeout(tick, next);
      });
    };
    this.pollTimer = setTimeout(tick, startupOffsetMs);
  }

  cleanup() {
    this.destroyed = true;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    this.pendingWrites.clear();
    this.desiredWrites.clear();
    clearTimeout(this.writerTimer);
    clearTimeout(this.staleTimer);
  }

  communicationError() {
    return new this.HAP.HapStatusError(this.HAP.HAPStatus.SERVICE_COMMUNICATION_FAILURE);
  }

  readState() {
    if (!this.state || this.fault || Date.now() - this.lastFetch >= MAX_STATE_AGE_MS) {
      throw this.communicationError();
    }
    let s = mergeStatus({}, this.state);
    for (const [path, { body }] of this.desiredWrites) {
      if (path === "/api/power") s = mergeStatus(s, { power_state: { turn_on: body.state === "ON" } });
      if (path === "/api/mode") s = mergeStatus(s, { operation_mode: { operation_mode: body.mode } });
      if (path === "/api/fan") s = mergeStatus(s, { fan_speed: { cooling_fan_speed: body.speed, heating_fan_speed: body.speed } });
      if (path === "/api/setpoint") {
        if (body.cooling !== undefined) s = mergeStatus(s, { set_point: { cooling_set_point: body.cooling } });
        if (body.heating !== undefined) s = mergeStatus(s, { set_point: { heating_set_point: body.heating } });
      }
    }
    return s;
  }

  scheduleExpiry() {
    clearTimeout(this.staleTimer);
    this.staleTimer = setTimeout(() => {
      if (!this.destroyed) this.pushAll();
    }, Math.max(0, this.lastFetch + MAX_STATE_AGE_MS - Date.now()));
  }

  activeFromState(s) {
    const { Characteristic } = this.HAP;
    return s?.power_state?.turn_on ? Characteristic.Active.ACTIVE : Characteristic.Active.INACTIVE;
  }

  currentStateFromState(s) {
    const { Characteristic } = this.HAP;
    if (!s?.power_state?.turn_on) return Characteristic.CurrentHeaterCoolerState.INACTIVE;
    const mode = s?.operation_mode?.operation_mode;
    if (mode === "COOL") return Characteristic.CurrentHeaterCoolerState.COOLING;
    if (mode === "HEAT") return Characteristic.CurrentHeaterCoolerState.HEATING;
    return Characteristic.CurrentHeaterCoolerState.IDLE;
  }

  targetStateFromState(s) {
    const { Characteristic } = this.HAP;
    const mode = s?.operation_mode?.operation_mode;
    if (mode === "HEAT") return Characteristic.TargetHeaterCoolerState.HEAT;
    if (mode === "COOL") return Characteristic.TargetHeaterCoolerState.COOL;
    return Characteristic.TargetHeaterCoolerState.AUTO;
  }

  displayTempFromState(s) {
    const indoor = s?.temperatures?.indoor;
    if (!Number.isFinite(indoor) || indoor < -50 || indoor > 100) throw this.communicationError();
    return indoor;
  }

  async fetchStatus(force = false) {
    const now = Date.now();
    if (!force && this.state && (now - this.lastFetch < 25_000)) return this.state;
    if (this.fetching) return this.fetching;
    const fetchPromise = this.platform.enqueue(this.device.id, async () => {
      if (this.destroyed) return this.state;
      const url = `${this.platform.baseUrl}/api/status?room=${encodeURIComponent(this.device.id)}${force ? "&force=1" : ""}`;
      try {
        const { status: httpStatus, body: data } = await httpJSON("GET", url);
        if (this.destroyed) return this.state;
        if (httpStatus >= 400 || data.ok !== true || data.verified === false ||
            typeof data.status?.power_state?.turn_on !== "boolean" ||
            typeof data.status?.operation_mode?.operation_mode !== "string" ||
            !Number.isFinite(data.status?.set_point?.cooling_set_point) ||
            !Number.isFinite(data.status?.set_point?.heating_set_point) ||
            (data.age_seconds !== undefined && (!Number.isFinite(data.age_seconds) || data.age_seconds < 0))) {
          throw new Error(data.error || `unverified or invalid status (HTTP ${httpStatus})`);
        }
        this.consecutiveUnreachable = 0;
        this.state = data.status;
        this.lastFetch = Date.now() - (data.age_seconds || 0) * 1000;
        this.fault = null;
        this.scheduleExpiry();
        return this.state;
      } catch (err) {
        this.consecutiveUnreachable += 1;
        if (this.consecutiveUnreachable <= 2 || this.consecutiveUnreachable % 10 === 0) {
          this.log.warn(`[${this.device.id}] status failed (#${this.consecutiveUnreachable}): ${err.message}`);
        }
        if (!this.state || Date.now() - this.lastFetch >= MAX_STATE_AGE_MS) this.fault = err;
        throw err;
      }
    }, {
      kind: "poll",
      priority: force ? 5 : 0,
      dedupeKey: force ? undefined : `poll:${this.device.id}`,
      skipIf: () => (
        this.destroyed ||
        (!force && this.state && (Date.now() - this.lastFetch < 55_000)) ||
        (!force && this.platform.hasQueuedWrite())
      ),
      skipValue: () => this.state,
    });
    const trackedPromise = fetchPromise.finally(() => {
      if (this.fetching === trackedPromise) this.fetching = null;
    });
    this.fetching = trackedPromise;
    return this.fetching;
  }

  async post(path, entry) {
    return this.platform.enqueue(this.device.id, async () => {
      if (this.destroyed || this.desiredWrites.get(path) !== entry) {
        this.log.debug(`[${this.device.id}] dropped superseded ${path}`);
        return;
      }
      if (Date.now() >= entry.expiresAt) throw new Error("command expired in queue; not sent");
      const { body } = entry;
      this.log.info(`[${this.device.id}] write ${path} body=${JSON.stringify(body)}`);
      const url = `${this.platform.baseUrl}${path}?room=${encodeURIComponent(this.device.id)}`;
      const { status: httpStatus, body: data } = await httpJSON("POST", url, body, {
        "X-Daikin-Expires-At": String(entry.expiresAt),
      });
      if (this.destroyed) return;
      if (httpStatus >= 400 || data.ok !== true || !data.status ||
          typeof data.status !== "object" || Array.isArray(data.status)) {
        this.log.warn(`[${this.device.id}] write ${path} http=${httpStatus}: ${data.error || "invalid write response"}`);
        throw new this.HAP.HapStatusError(this.HAP.HAPStatus.SERVICE_COMMUNICATION_FAILURE);
      }
      if (data.status) {
        this.state = mergeStatus(this.state || {}, data.status);
      }
    }, {
      kind: "write",
      priority: path === "/api/power" && entry.body.state === "OFF" ? 20 : 10,
    });
  }

  // Keep optimistic intent separate from confirmed state. HomeKit setters
  // cannot wait for BLE; a short debounce coalesces a single Home gesture.
  writeAsync(path, body) {
    if (this.destroyed) return;
    if (path === "/api/setpoint" && Object.values(body).some((value) =>
      !Number.isInteger(value) || value < 16 || value > 30)) {
      this.log.warn(`[${this.device.id}] rejected setpoint: pymadoka supports writes from 16 to 30 C`);
      throw new this.HAP.HapStatusError(this.HAP.HAPStatus.INVALID_VALUE_IN_REQUEST);
    }
    this.log.info(`[${this.device.id}] HomeKit request ${path} body=${JSON.stringify(body)}`);
    const previous = this.desiredWrites.get(path);
    let expiresAt = Date.now() + WRITE_TIMEOUT_MS;
    if (previous && path === "/api/setpoint" && previous.expiresAt > Date.now()) {
      body = { ...previous.body, ...body };
      expiresAt = previous.expiresAt;
    }
    const entry = { body, expiresAt };
    this.desiredWrites.set(path, entry);
    this.pendingWrites.set(path, entry);
    this.pushAll();
    if (!this.writerActive) {
      this.writerActive = true;
      this.writerTimer = setTimeout(() => {
        this.runWriter().catch((e) => {
          this.log.warn(`[${this.device.id}] writer pump crashed: ${e.message}`);
          this.fault = e;
          this.pushAll();
        });
      }, 150);
    }
  }

  async runWriter() {
    try {
      while (!this.destroyed && this.pendingWrites.size > 0) {
        const path = this.pendingWrites.get("/api/power")?.body.state === "OFF"
          ? "/api/power" : this.pendingWrites.keys().next().value;
        const entry = this.pendingWrites.get(path);
        this.pendingWrites.delete(path);
        try {
          await this.post(path, entry);
        } catch (err) {
          this.log.warn(`[${this.device.id}] write ${path} failed: ${err.message}; not replaying; checking actual state`);
          this.fault = err;
        } finally {
          if (this.desiredWrites.get(path) === entry) this.desiredWrites.delete(path);
          if (!this.destroyed) this.pushAll();
        }
      }
    } finally {
      this.writerActive = false;
      if (!this.destroyed) await this.refresh(true);
    }
  }

  async refresh(force = false) {
    try {
      await this.fetchStatus(force);
    } catch (_) { /* fetchStatus logs failures; pushAll exposes expired state. */ }
    if (!this.destroyed) this.pushAll();
  }

  pushAll() {
    const { Characteristic } = this.HAP;
    const svc = this.service;
    let s;
    try {
      s = this.readState();
    } catch (err) {
      for (const name of ["Active", "CurrentHeaterCoolerState", "TargetHeaterCoolerState",
        "CurrentTemperature", "CoolingThresholdTemperature", "HeatingThresholdTemperature", "RotationSpeed"]) {
        svc.getCharacteristic(Characteristic[name]).updateValue(err);
      }
      return;
    }
    svc.updateCharacteristic(Characteristic.Active, this.activeFromState(s));
    svc.updateCharacteristic(Characteristic.CurrentHeaterCoolerState, this.currentStateFromState(s));
    svc.updateCharacteristic(Characteristic.TargetHeaterCoolerState, this.targetStateFromState(s));
    try {
      svc.updateCharacteristic(Characteristic.CurrentTemperature, this.displayTempFromState(s));
    } catch (err) {
      svc.getCharacteristic(Characteristic.CurrentTemperature).updateValue(err);
    }
    if (typeof s.set_point?.cooling_set_point === "number") {
      svc.updateCharacteristic(Characteristic.CoolingThresholdTemperature, s.set_point.cooling_set_point);
    }
    if (typeof s.set_point?.heating_set_point === "number") {
      svc.updateCharacteristic(Characteristic.HeatingThresholdTemperature, s.set_point.heating_set_point);
    }
    const fan = s.fan_speed?.cooling_fan_speed || s.fan_speed?.heating_fan_speed;
    const fanVal = ({ LOW: 25, MID: 50, HIGH: 75, AUTO: 100 })[fan];
    if (fanVal !== undefined) svc.updateCharacteristic(Characteristic.RotationSpeed, fanVal);
  }
}

function mergeStatus(base, patch) {
  if (!patch || typeof patch !== "object") return base;
  const out = { ...(base || {}) };
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      out[key] = mergeStatus(out[key] || {}, value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

function httpJSON(method, url, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const opts = {
      method,
      hostname: u.hostname,
      port: u.port || 80,
      path: u.pathname + u.search,
      headers: {
        accept: "application/json",
        ...headers,
        ...(data ? {
          "content-type": "application/json",
          "content-length": data.length,
        } : {}),
      },
      timeout: 75_000, // pymadoka calls can take 60+ seconds
    };
    const req = http.request(opts, (res) => {
      let buf = "";
      res.setEncoding("utf8");
      res.on("error", reject);
      res.on("aborted", () => reject(new Error("backend response aborted")));
      res.on("data", (chunk) => (buf += chunk));
      res.on("end", () => {
        let parsed = {};
        if (buf) {
          try {
            parsed = JSON.parse(buf);
          } catch (err) {
            return reject(new Error(`bad json from ${url} (http ${res.statusCode}): ${err.message}`));
          }
        }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          return reject(new Error("backend response must be a JSON object"));
        }
        resolve({ status: res.statusCode || 0, body: parsed });
      });
    });
    req.on("timeout", () => req.destroy(new Error("request timed out")));
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}
