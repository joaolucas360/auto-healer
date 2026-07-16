import importlib.util
import json
from pathlib import Path

import requests


MODULE_PATH = Path(__file__).resolve().parents[1] / "main.py"
spec = importlib.util.spec_from_file_location("healer_main", MODULE_PATH)
healer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healer)


class FakeResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


def test_get_int_env_uses_default_for_invalid_value(monkeypatch):
    monkeypatch.setenv("CHECK_INTERVAL", "invalid")

    assert healer.get_int_env("CHECK_INTERVAL", 10, minimum=1) == 10


def test_get_int_env_uses_default_below_minimum(monkeypatch):
    monkeypatch.setenv("CHECK_INTERVAL", "0")

    assert healer.get_int_env("CHECK_INTERVAL", 10, minimum=1) == 10


def test_save_incident_appends_to_json_ledger(tmp_path, monkeypatch):
    incident_path = tmp_path / "incidents.json"
    monkeypatch.setattr(healer, "INCIDENT_LOG_PATH", incident_path)

    healer.save_incident("hung", "App is hung", 2)
    healer.save_incident("memory_leak", "Memory leak detected", 3)

    incidents = healer.load_incidents()
    assert [incident["id"] for incident in incidents] == [1, 2]
    assert [incident["type"] for incident in incidents] == ["hung", "memory_leak"]


def test_query_prometheus_success(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse({
            "status": "success",
            "data": {"result": [{"value": [123, "42"]}]},
        })

    monkeypatch.setattr(healer.session, "get", fake_get)

    assert healer.query_prometheus("app_is_hung") == 42.0


def test_query_prometheus_timeout(monkeypatch):
    def fake_get(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(healer.session, "get", fake_get)

    assert healer.query_prometheus("app_is_hung") == 0.0


def test_query_prometheus_http_error(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse({}, requests.HTTPError("boom"))

    monkeypatch.setattr(healer.session, "get", fake_get)

    assert healer.query_prometheus("app_is_hung") == 0.0


def test_query_prometheus_invalid_response(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse({"status": "error", "error": "bad query"})

    monkeypatch.setattr(healer.session, "get", fake_get)

    assert healer.query_prometheus("app_is_hung") == 0.0


def test_check_and_heal_respects_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(healer, "RESTART_COOLDOWN_SECONDS", 60)
    monkeypatch.setattr(healer, "last_restart_at", 100.0)
    monkeypatch.setattr(healer.time, "time", lambda: 120.0)
    monkeypatch.setattr(healer, "query_prometheus", lambda query: 1.0)
    monkeypatch.setattr(healer, "restart_container", lambda *args: calls.append(args) or True)

    healer.check_and_heal()

    assert calls == []


def test_check_and_heal_restarts_hung_app(monkeypatch):
    calls = []
    monkeypatch.setattr(healer, "RESTART_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(healer, "last_restart_at", 0.0)
    monkeypatch.setattr(healer.time, "time", lambda: 200.0)
    monkeypatch.setattr(
        healer,
        "query_prometheus",
        lambda query: 1.0 if query == "app_is_hung" else 0.0,
    )
    monkeypatch.setattr(healer, "restart_container", lambda *args: calls.append(args) or True)

    healer.check_and_heal()

    assert calls == [("hung", "App is hung (app_is_hung=1)")]
    assert healer.last_restart_at == 200.0


def test_check_and_heal_restarts_memory_leak(monkeypatch):
    calls = []
    monkeypatch.setattr(healer, "RESTART_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(healer, "last_restart_at", 0.0)
    monkeypatch.setattr(healer, "MEMORY_LEAK_THRESHOLD", 100)
    monkeypatch.setattr(healer.time, "time", lambda: 300.0)
    monkeypatch.setattr(
        healer,
        "query_prometheus",
        lambda query: 150.0 if query == "app_memory_leak_bytes" else 0.0,
    )
    monkeypatch.setattr(healer, "restart_container", lambda *args: calls.append(args) or True)

    healer.check_and_heal()

    assert calls[0][0] == "memory_leak"
    assert "Memory leak detected" in calls[0][1]
    assert healer.last_restart_at == 300.0


def test_restart_container_uses_docker_and_saves_incident(monkeypatch):
    calls = []

    class FakeContainer:
        def restart(self, timeout):
            calls.append(("restart", timeout))

    class FakeContainers:
        def get(self, name):
            calls.append(("get", name))
            return FakeContainer()

    class FakeDockerClient:
        containers = FakeContainers()

    monkeypatch.setattr(healer.docker, "from_env", lambda: FakeDockerClient())
    monkeypatch.setattr(healer, "save_incident", lambda *args: calls.append(("save", args)) or True)

    assert healer.restart_container("hung", "App is hung") is True
    assert calls[0] == ("get", healer.VICTIM_CONTAINER)
    assert ("restart", 10) in calls
    assert calls[-1] == ("save", ("hung", "App is hung", 0))


def test_load_incidents_handles_invalid_valid_json_shapes(tmp_path, monkeypatch):
    incident_path = tmp_path / "incidents.json"
    monkeypatch.setattr(healer, "INCIDENT_LOG_PATH", incident_path)

    for payload in (None, [], "invalid", {"items": []}, {"incidents": "bad"}):
        incident_path.write_text(json.dumps(payload))
        assert healer.load_incidents() == []


def test_restart_success_ignores_persistence_failure_for_cooldown(monkeypatch):
    class FakeContainer:
        def restart(self, timeout):
            return None

    class FakeContainers:
        def get(self, name):
            return FakeContainer()

    class FakeDockerClient:
        containers = FakeContainers()

    monkeypatch.setattr(healer.docker, "from_env", lambda: FakeDockerClient())
    monkeypatch.setattr(healer, "save_incident", lambda *args: False)

    assert healer.restart_container("hung", "App is hung") is True
