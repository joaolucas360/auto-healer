import pytest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from main import app, _state

client = TestClient(app)

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
    initial_memory = len(_state["memory_hog"])
    response = client.post("/chaos/memory-leak")
    assert response.status_code == 200
    assert "Memory leak incrementado" in response.json()["message"]
    
    final_memory = len(_state["memory_hog"])
    assert final_memory == initial_memory + 1

def test_chaos_reset():
    # Setup some chaotic state
    client.post("/chaos/memory-leak")
    client.post("/chaos/hang")
    
    # Assert state is chaotic
    assert _state["hung"] is True
    assert len(_state["memory_hog"]) > 0
    
    # Reset
    response = client.post("/chaos/reset")
    assert response.status_code == 200
    assert response.json()["message"] == "Estado resetado ao normal"
    
    # Assert normal state
    assert _state["hung"] is False
    assert len(_state["memory_hog"]) == 0
