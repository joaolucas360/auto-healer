import pytest
import sys
import os
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_state():
    main.CHAOS_TOKEN = ""
    with main._state_lock:
        main._state["memory_hog"] = []
        main._state["hung"] = False
    main.memory_leak_gauge.set(0)
    main.app_hung_gauge.set(0)
    yield
    main.CHAOS_TOKEN = ""


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "hung": False}


def test_data_ok():
    response = client.get("/data")
    assert response.status_code == 200
    assert "message" in response.json()
    assert "timestamp" in response.json()


def test_chaos_memory_leak():
    initial_memory = len(main._state["memory_hog"])
    response = client.post("/chaos/memory-leak")
    assert response.status_code == 200
    assert "Memory leak incrementado" in response.json()["message"]
    
    final_memory = len(main._state["memory_hog"])
    assert final_memory == initial_memory + 1


def test_chaos_reset():
    # Setup some chaotic state
    client.post("/chaos/memory-leak")
    client.post("/chaos/hang")
    
    # Assert state is chaotic
    assert main._state["hung"] is True
    assert len(main._state["memory_hog"]) > 0
    
    # Reset
    response = client.post("/chaos/reset")
    assert response.status_code == 200
    assert response.json()["message"] == "Estado resetado ao normal"
    
    # Assert normal state
    assert main._state["hung"] is False
    assert len(main._state["memory_hog"]) == 0


def test_invalid_hang_delay_uses_default(caplog, monkeypatch):
    monkeypatch.setenv("HANG_DELAY_SECONDS", "invalid")

    value = main.get_int_env("HANG_DELAY_SECONDS", 10, minimum=0)

    assert value == 10
    assert "Invalid HANG_DELAY_SECONDS='invalid', using default 10" in caplog.text


def test_chaos_token_blocks_when_missing():
    main.CHAOS_TOKEN = "secret"

    response = client.post("/chaos/hang")

    assert response.status_code == 403
    assert main._state["hung"] is False


def test_chaos_token_blocks_when_incorrect():
    main.CHAOS_TOKEN = "secret"

    response = client.post("/chaos/hang", headers={"x-chaos-token": "wrong"})

    assert response.status_code == 403
    assert main._state["hung"] is False


def test_chaos_token_allows_matching_header():
    main.CHAOS_TOKEN = "secret"

    response = client.post("/chaos/hang", headers={"x-chaos-token": "secret"})

    assert response.status_code == 200
    assert main._state["hung"] is True


def test_memory_metric_matches_state_after_concurrent_operations(monkeypatch):
    monkeypatch.setattr(main, "MEMORY_LEAK_CHUNK_BYTES", 1)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: main.chaos_memory_leak(), range(25)))

    with main._state_lock:
        expected_bytes = len(main._state["memory_hog"]) * main.MEMORY_LEAK_CHUNK_BYTES

    assert expected_bytes == 25
    assert main.memory_leak_gauge._value.get() == expected_bytes


def test_reset_metrics_match_state():
    client.post("/chaos/memory-leak")
    client.post("/chaos/hang")

    response = client.post("/chaos/reset")

    assert response.status_code == 200
    assert main.memory_leak_gauge._value.get() == 0
    assert main.app_hung_gauge._value.get() == 0
