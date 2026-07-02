from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Gauge, Counter
from typing import Dict, Any
import time
import threading
import os

app = FastAPI(title="Victim App - Auto-Healer Demo")

# ---- Metrics ----
memory_leak_gauge = Gauge("app_memory_leak_bytes", "Artificially consumed memory by chaos endpoint")
chaos_events_total = Counter("app_chaos_events_total", "Chaos events triggered", ["type"])
app_hung_gauge = Gauge("app_is_hung", "1 if app is in a simulated hung state, 0 otherwise")

# ---- Internal State ----
_state: Dict[str, Any] = {
    "memory_hog": [],
    "hung": False,
}

instrumentator = Instrumentator().instrument(app)


@app.on_event("startup")
async def startup() -> None:
    """Initializes the Prometheus instrumentator on app startup."""
    instrumentator.expose(app)


@app.get("/health")
def health() -> Dict[str, Any]:
    """Returns the health status of the application. May hang if state is hung."""
    try:
        if _state["hung"]:
            time.sleep(10)
        return {"status": "ok", "hung": _state["hung"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")


@app.get("/data")
def data() -> Dict[str, Any]:
    """Returns sample application data. May hang if state is hung."""
    try:
        if _state["hung"]:
            time.sleep(10)
        return {"message": "dados normais da aplicação", "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Data retrieval failed: {str(e)}")


@app.post("/chaos/crash")
def chaos_crash() -> Dict[str, str]:
    """Schedules a forced application crash (exit 1) in 0.5 seconds."""
    try:
        chaos_events_total.labels(type="crash").inc()
        threading.Timer(0.5, lambda: os._exit(1)).start()
        return {"message": "Crash agendado em 0.5s"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to schedule crash: {str(e)}")


@app.post("/chaos/memory-leak")
def chaos_memory_leak() -> Dict[str, Any]:
    """Increments artificial memory consumption to simulate a memory leak."""
    try:
        chaos_events_total.labels(type="memory_leak").inc()
        _state["memory_hog"].append(bytearray(50 * 1024 * 1024))
        total_bytes = len(_state["memory_hog"]) * 50 * 1024 * 1024
        memory_leak_gauge.set(total_bytes)
        return {"message": "Memory leak incrementado", "total_mb": total_bytes / (1024 * 1024)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger memory leak: {str(e)}")


@app.post("/chaos/hang")
def chaos_hang() -> Dict[str, str]:
    """Sets the application state to hung, causing delays in endpoints."""
    try:
        chaos_events_total.labels(type="hang").inc()
        _state["hung"] = True
        app_hung_gauge.set(1)
        return {"message": "App agora está 'hung' (respostas lentas/travadas)"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger hang: {str(e)}")


@app.post("/chaos/reset")
def chaos_reset() -> Dict[str, str]:
    """Resets the application state to normal, clearing memory leaks and hung state."""
    try:
        chaos_events_total.labels(type="reset").inc()
        _state["memory_hog"] = []
        _state["hung"] = False
        memory_leak_gauge.set(0)
        app_hung_gauge.set(0)
        return {"message": "Estado resetado ao normal"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset state: {str(e)}")
