import copy
import importlib.util
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


spec = importlib.util.spec_from_file_location(
    "daikin_web", Path(__file__).resolve().parents[1] / "src" / "daikin_web.py"
)
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)

DEVICE = {"id": "test_room", "name": "Test room", "mac": "AA:BB:CC:DD:EE:01"}
STATUS = {
    "power_state": {"turn_on": False},
    "operation_mode": {"operation_mode": "HEAT"},
    "set_point": {"cooling_set_point": 29, "heating_set_point": 20},
    "temperatures": {"indoor": 18.5},
}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        web.STATUS_CACHE.clear()
        web.FAILURES.clear()
        web._DEVICE_LOCKS.clear()

    def test_explicit_off_is_not_skipped_after_cached_off(self):
        web._cache_set(DEVICE, copy.deepcopy(STATUS))
        with patch.object(web, "run_pymadoka", return_value={}) as run:
            web.handle_action(DEVICE, {"action": "power", "state": "OFF"})
        self.assertEqual(run.call_args.args[1:], ("set-power-state", "OFF"))

    def test_partial_setpoint_preserves_live_other_side(self):
        stale = copy.deepcopy(STATUS)
        stale["set_point"]["cooling_set_point"] = 24
        web._cache_set(DEVICE, stale)
        with patch.object(web, "run_pymadoka", side_effect=[copy.deepcopy(STATUS), {}]) as run:
            result = web.handle_action(DEVICE, {"action": "setpoint", "heating": 22})
        self.assertEqual(run.call_args_list[0].args[1:], ("get-status",))
        self.assertEqual(run.call_args_list[1].args[1:], ("set-set-point", "29", "22"))
        self.assertEqual(result["set_point"]["heating_set_point"], 22)

    def test_legacy_value_resolves_mode_after_cold_read(self):
        with patch.object(web, "run_pymadoka", side_effect=[copy.deepcopy(STATUS), {}]) as run:
            web.handle_action(DEVICE, {"action": "setpoint", "value": 21})
        self.assertEqual(run.call_args.args[1:], ("set-set-point", "29", "21"))

    def test_partial_setpoint_does_not_fall_back_to_stale_values(self):
        web._cache_set(DEVICE, copy.deepcopy(STATUS))
        with patch.object(web, "run_pymadoka", side_effect=RuntimeError("timeout")) as run:
            with self.assertRaises(web.DeviceError):
                web.handle_action(DEVICE, {"action": "setpoint", "heating": 22})
        self.assertEqual(run.call_count, 1)

    def test_both_setpoints_do_not_require_preread(self):
        with patch.object(web, "run_pymadoka", return_value={}) as run:
            web.handle_action(DEVICE, {"action": "setpoint", "cooling": 30, "heating": 21})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[1:], ("set-set-point", "30", "21"))

    def test_invalid_setpoints_never_touch_bluetooth(self):
        for payload in ({}, {"cooling": True}, {"heating": 31}, {"cooling": 31},
                        {"value": 22, "heating": 21}, {"cooling": 22.5}):
            with self.subTest(payload=payload), patch.object(web, "run_pymadoka") as run:
                with self.assertRaises(ValueError):
                    web.handle_action(DEVICE, {"action": "setpoint", **payload})
                run.assert_not_called()

    def test_partial_write_cannot_silently_clamp_other_side_from_31_to_30(self):
        state = copy.deepcopy(STATUS)
        state["set_point"]["cooling_set_point"] = 31
        with patch.object(web, "run_pymadoka", return_value=state) as run:
            with self.assertRaisesRegex(ValueError, "cannot preserve"):
                web.handle_action(DEVICE, {"action": "setpoint", "heating": 22})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[1:], ("get-status",))

    def test_expired_write_never_touches_bluetooth(self):
        with patch.object(web, "run_pymadoka") as run:
            with self.assertRaisesRegex(web.DeviceError, "expired"):
                web.handle_action(DEVICE, {"action": "power", "state": "ON"}, time.monotonic() - 1)
            run.assert_not_called()

    def test_ble_lock_wait_has_deadline(self):
        web.BLE_LOCK.acquire()
        try:
            with patch.object(web.subprocess, "run") as run:
                with self.assertRaisesRegex(web.DeviceError, "expired"):
                    web.run_pymadoka(DEVICE["mac"], "set-power-state", "ON",
                                    deadline=time.monotonic() + 0.02)
                run.assert_not_called()
        finally:
            web.BLE_LOCK.release()

    def test_subprocess_timeout_is_limited_by_remaining_command_lifetime(self):
        proc = subprocess.CompletedProcess([], 0, '{"turn_on":true}', "")
        with patch.object(web, "POST_CALL_SETTLE", 0), \
                patch.object(web, "_stop_ble_discovery"), \
                patch.object(web.time, "monotonic", return_value=100), \
                patch.object(web.subprocess, "run", return_value=proc) as run:
            web.run_pymadoka(DEVICE["mac"], "set-power-state", "ON", deadline=110)
            self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_expiry_is_checked_again_after_scan_cleanup(self):
        with patch.object(web, "POST_CALL_SETTLE", 0), \
                patch.object(web, "_stop_ble_discovery"), \
                patch.object(web.time, "monotonic", side_effect=[100, 100, 111]), \
                patch.object(web.subprocess, "run") as run:
            with self.assertRaisesRegex(web.DeviceError, "expired"):
                web.run_pymadoka(DEVICE["mac"], "set-power-state", "ON", deadline=110)
            run.assert_not_called()

    def test_write_timeout_is_never_retried_even_if_read_retries_enabled(self):
        with patch.object(web, "PYMADOKA_RETRIES", 2), \
                patch.object(web, "POST_CALL_SETTLE", 0), \
                patch.object(web, "_stop_ble_discovery"), \
                patch.object(web, "_cleanup_ble_device"), \
                patch.object(web.subprocess, "run", side_effect=subprocess.TimeoutExpired("pymadoka", 40)) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                web.run_pymadoka(DEVICE["mac"], "set-power-state", "ON")
        self.assertEqual(run.call_count, 1)

    def test_incomplete_status_is_not_treated_as_off(self):
        incomplete = copy.deepcopy(STATUS)
        incomplete["set_point"] = {}
        for data in ({"temperatures": {"indoor": 20}}, incomplete):
            proc = subprocess.CompletedProcess([], 0, json.dumps(data), "")
            with self.subTest(data=data), patch.object(web, "POST_CALL_SETTLE", 0), \
                    patch.object(web, "_stop_ble_discovery"), \
                    patch.object(web.subprocess, "run", return_value=proc):
                with self.assertRaisesRegex(RuntimeError, "incomplete status"):
                    web.run_pymadoka(DEVICE["mac"], "get-status")

    def test_reads_can_still_retry_transient_errors(self):
        responses = [
            subprocess.CompletedProcess([], 1, "", "le-connection-abort"),
            subprocess.CompletedProcess([], 0, json.dumps(STATUS), ""),
        ]
        with patch.object(web, "PYMADOKA_RETRIES", 1), \
                patch.object(web, "PYMADOKA_RETRY_BACKOFF", 0), \
                patch.object(web, "POST_CALL_SETTLE", 0), \
                patch.object(web, "_stop_ble_discovery"), \
                patch.object(web.subprocess, "run", side_effect=responses) as run:
            self.assertEqual(web.run_pymadoka(DEVICE["mac"], "get-status"), STATUS)
            self.assertEqual(run.call_count, 2)

    def test_power_write_bypasses_background_failure_cooldown(self):
        web.record_failure(DEVICE, RuntimeError("timeout"))
        with patch.object(web, "run_pymadoka", return_value={"turn_on": False}) as run:
            web.handle_action(DEVICE, {"action": "power", "state": "OFF"})
        self.assertEqual(run.call_args.args[1:], ("set-power-state", "OFF"))

    def test_zero_exit_with_empty_malformed_or_wrong_write_ack_is_failure(self):
        for output in ("", "not JSON", "null", "{}", '{"turn_on":false}', '{"turn_on":1}'):
            proc = subprocess.CompletedProcess([], 0, output, "connection failed")
            with self.subTest(output=output), patch.object(web, "POST_CALL_SETTLE", 0), \
                    patch.object(web, "_stop_ble_discovery"), \
                    patch.object(web.subprocess, "run", return_value=proc) as run:
                with self.assertRaisesRegex(RuntimeError, "acknowledgement|non-JSON"):
                    web.run_pymadoka(DEVICE["mac"], "set-power-state", "ON")
                self.assertEqual(run.call_count, 1)

    def test_successful_write_acknowledgement(self):
        proc = subprocess.CompletedProcess([], 0, '{"turn_on":true}', "")
        with patch.object(web, "POST_CALL_SETTLE", 0), \
                patch.object(web, "_stop_ble_discovery"), \
                patch.object(web.subprocess, "run", return_value=proc):
            self.assertEqual(web.run_pymadoka(DEVICE["mac"], "set-power-state", "ON"), {"turn_on": True})

    def test_health_is_cache_only_and_reports_unverified_state(self):
        with self.server() as url, patch.object(web, "run_pymadoka") as run:
            with urlopen(f"{url}/healthz", timeout=2) as response:
                data = json.load(response)
            self.assertTrue(data["ok"])
            self.assertTrue(all(not item["verified"] for item in data["devices"].values()))
            run.assert_not_called()

    def test_status_read_waits_for_same_room_write_lock(self):
        entered = threading.Event()
        finished = threading.Event()

        def read():
            entered.set()
            web.get_status_full(DEVICE, force=True)
            finished.set()

        with patch.object(web, "run_pymadoka", return_value=copy.deepcopy(STATUS)) as run:
            with web._device_lock(DEVICE):
                thread = threading.Thread(target=read)
                thread.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(finished.wait(0.03))
                run.assert_not_called()
            thread.join(1)
            self.assertTrue(finished.is_set())

    def test_http_status_failure_is_not_success_shaped_stale_cache(self):
        web._cache_set(DEVICE, copy.deepcopy(STATUS))
        web.STATUS_CACHE[DEVICE["id"]]["ts"] -= web.STATUS_TTL + 1
        with self.server() as url, patch.object(web, "run_pymadoka", side_effect=RuntimeError("timeout")):
            with self.assertRaises(HTTPError) as caught:
                urlopen(f"{url}/api/status?room=test_room", timeout=2)
            self.assertEqual(caught.exception.code, 503)
            self.assertFalse(json.load(caught.exception)["ok"])
            caught.exception.close()

    def test_http_expired_command_is_rejected(self):
        with self.server() as url, patch.object(web, "run_pymadoka") as run:
            request = Request(f"{url}/api/power?room=test_room", data=b'{"state":"ON"}',
                              headers={"X-Daikin-Expires-At": str((time.time() - 1) * 1000)})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            self.assertEqual(json.load(caught.exception)["code"], "expired")
            caught.exception.close()
            run.assert_not_called()

    def server(self):
        from contextlib import contextmanager

        @contextmanager
        def running():
            with patch.dict(web.DEVICE_BY_ID, {DEVICE["id"]: DEVICE}, clear=True):
                server = web.DaikinHTTPServer(("127.0.0.1", 0), web.Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    yield f"http://127.0.0.1:{server.server_port}"
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
        return running()


if __name__ == "__main__":
    unittest.main()
