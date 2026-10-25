"""Actual Flask routes with mocked motors/eyes; no hardware imports or workers."""

import ast
import builtins
import logging
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask, jsonify
from robot_web_security import install_api_security, load_web_settings

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a-secure-random-test-token-0123456789"


def web_namespace(source=None):
    tree = ast.parse(source if source is not None else (ROOT / "robot_backend.py").read_text())
    # Execute the unchanged owning web routes, never module-level hardware setup.
    start = next(i for i, node in enumerate(tree.body)
                 if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id == "app" for target in node.targets))
    end = next(i for i, node in enumerate(tree.body[start:], start)
               if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'")
    namespace = {"__name__": "robot_web_test", "__file__": str(ROOT / "robot_backend.py"),
                 "Flask": Flask, "jsonify": jsonify, "os": os, "logging": logging,
                 "install_api_security": install_api_security, "API_TOKEN": TOKEN,
                 "state_lock": threading.Lock(), "ui_state": {"sensitivity": 50, "gain": 50},
                 "autonomous_mode": False}
    for name in ["drive", "stop_robot", "clear_motor_queue", "robot_eyes", "dance_engine"]:
        namespace[name] = Mock(name=name)
    exec(compile(ast.Module(body=tree.body[start:end], type_ignores=[]), "robot_backend.py:web-routes", "exec"), namespace)
    namespace["convert_to_json_serializable"] = lambda value: value
    namespace["app"].config["TESTING"] = True
    return namespace


class WebSecurityTests(unittest.TestCase):
    def setUp(self):
        self.ns = web_namespace()
        self.client = self.ns["app"].test_client()

    def test_missing_or_wrong_credentials_cannot_reach_any_control_or_status(self):
        endpoints = ["/api/control/" + command for command in
                     ["forward", "backward", "left", "right", "stop", "toggle_auto"]]
        endpoints += ["/api/sens/75", "/api/gain/25", "/api/eyes/happy", "/api/eyes/special/star"]
        for token in [None, "wrong", TOKEN + "x", "é" * 33]:
            headers = {} if token is None else {"Authorization": "Bearer " + token}
            for path in endpoints:
                with self.subTest(path=path, token_kind="missing" if token is None else "invalid"):
                    response = self.client.post(path, headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(self.client.get("/api/status", headers=headers).status_code, 401)
        self.assertFalse(self.ns["autonomous_mode"])
        self.assertEqual(self.ns["ui_state"], {"sensitivity": 50, "gain": 50})
        for name in ["drive", "stop_robot", "clear_motor_queue", "robot_eyes", "dance_engine"]:
            self.assertEqual(self.ns[name].mock_calls, [])

    def test_query_cookie_form_and_basic_auth_never_replace_bearer_header(self):
        for kwargs in [{"query_string": {"token": TOKEN}}, {"data": {"token": TOKEN}},
                       {"headers": {"Authorization": "Basic " + TOKEN}},
                       {"headers": {"Authorization": "Bearer " + "x" * 257}}]:
            self.assertEqual(self.client.post("/api/control/forward", **kwargs).status_code, 401)
        self.client.set_cookie("token", TOKEN)
        self.assertEqual(self.client.post("/api/control/forward").status_code, 401)
        self.ns["drive"].assert_not_called()

    def test_cross_origin_simple_form_is_rejected_without_cors_permission(self):
        response = self.client.post("/api/control/forward", data={}, headers={"Origin": "https://attacker.invalid"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.ns["drive"].assert_not_called()

    def test_authorized_motor_stop_autonomous_settings_eyes_and_status_work(self):
        headers = {"Authorization": "Bearer " + TOKEN}
        for command in ["forward", "backward", "left", "right"]:
            self.assertEqual(self.client.post("/api/control/" + command, headers=headers).status_code, 200)
            self.ns["drive"].assert_called_with(command, 100, 100)
        self.assertEqual(self.client.post("/api/control/stop", headers=headers).status_code, 200)
        self.ns["stop_robot"].assert_called_once()
        self.assertEqual(self.client.post("/api/control/toggle_auto", headers=headers).status_code, 200)
        self.assertTrue(self.ns["autonomous_mode"])
        self.assertEqual(self.client.post("/api/sens/75", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/gain/25", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/eyes/happy", headers=headers).status_code, 200)
        self.ns["robot_eyes"].set_expression.assert_called_with("happy")
        self.assertEqual(self.client.post("/api/eyes/special/star", headers=headers).status_code, 200)
        self.ns["robot_eyes"].trigger_special.assert_called_once_with("star")
        result = self.client.get("/api/status", headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["sensitivity"], 75)
        self.assertEqual(result.json["gain"], 25)

    def test_public_frontend_remains_accessible_without_disclosing_token(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(TOKEN, response.get_data(as_text=True))

    def test_configuration_fails_closed_and_defaults_to_loopback(self):
        for token in ["", "short", "x" * 257, "x" * 32 + "\n", "é" * 32]:
            with self.assertRaises(RuntimeError):
                load_web_settings({"ROBOT_API_TOKEN": token})
        self.assertEqual(load_web_settings({"ROBOT_API_TOKEN": TOKEN}), ("127.0.0.1", TOKEN))
        self.assertEqual(load_web_settings({"ROBOT_API_TOKEN": TOKEN, "ROBOT_BIND_HOST": "192.0.2.10"}), ("192.0.2.10", TOKEN))
        with self.assertRaises(RuntimeError):
            load_web_settings({"ROBOT_API_TOKEN": TOKEN, "ROBOT_BIND_HOST": "attacker.invalid"})

    def test_missing_token_stops_full_module_before_hardware_import(self):
        original_import = builtins.__import__
        hardware_imports = []
        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ["numpy", "scipy", "sounddevice", "RPi", "board", "busio", "adafruit_ht16k33"]:
                hardware_imports.append(name)
                raise AssertionError("Hardware/scientific imports must not occur without authentication configuration")
            return original_import(name, *args, **kwargs)
        with patch.dict(os.environ, {}, clear=True), patch("builtins.__import__", guarded_import):
            with self.assertRaisesRegex(RuntimeError, "ROBOT_API_TOKEN"):
                exec(compile((ROOT / "robot_backend.py").read_text(), "robot_backend.py", "exec"), {"__name__": "robot_backend"})
        self.assertEqual(hardware_imports, [])


if __name__ == "__main__":
    unittest.main()
