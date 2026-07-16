from fastapi import Depends, FastAPI, Header, HTTPException, status
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Gauge, Counter
from typing import Dict, Any
import logging
import time
import threading
import os

log = logging.getLogger(__name__)
app = FastAPI(title="Victim App - Auto-Healer Demo")


def get_int_env(name: str, default: int, minimum: int = 0) -> int:
    """Reads an integer env var without breaking application import."""
    value = os.getenv(name)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        log.warning("Invalid %s=%r, using default %s", name, value, default)
        return default

    if parsed < minimum:
        log.warning("%s must be >= %s, using default %s", name, minimum, default)
        return default

    return parsed


MEMORY_LEAK_CHUNK_BYTES = 50 * 1024 * 1024
HANG_DELAY_SECONDS = get_int_env("HANG_DELAY_SECONDS", 10, minimum=0)
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")

# ---- Metrics ----
memory_leak_gauge = Gauge("app_memory_leak_bytes", "Artificially consumed memory by chaos endpoint")
chaos_events_total = Counter("app_chaos_events_total", "Chaos events triggered", ["type"])
app_hung_gauge = Gauge("app_is_hung", "1 if app is in a simulated hung state, 0 otherwise")

# ---- Internal State ----
_state: Dict[str, Any] = {
    "memory_hog": [],
    "hung": False,
}
_state_lock = threading.Lock()

instrumentator = Instrumentator().instrument(app)
instrumentator.expose(app)


def require_chaos_token(x_chaos_token: str | None = Header(default=None)) -> None:
    """Protects destructive demo endpoints when CHAOS_TOKEN is configured."""
    if CHAOS_TOKEN and x_chaos_token != CHAOS_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid chaos token",
        )


@app.get("/health")
def health() -> Dict[str, Any]:
    """Returns the health status of the application. May hang if state is hung."""
    with _state_lock:
        hung = bool(_state["hung"])

    if hung:
        time.sleep(HANG_DELAY_SECONDS)

    return {"status": "ok", "hung": hung}


@app.get("/data")
def data() -> Dict[str, Any]:
    """Returns sample application data. May hang if state is hung."""
    with _state_lock:
        hung = bool(_state["hung"])

    if hung:
        time.sleep(HANG_DELAY_SECONDS)

    return {"message": "dados normais da aplicação", "timestamp": time.time()}


@app.post("/chaos/crash", dependencies=[Depends(require_chaos_token)])
def chaos_crash() -> Dict[str, str]:
    """Schedules a forced application crash (exit 1) in 0.5 seconds."""
    chaos_events_total.labels(type="crash").inc()
    threading.Timer(0.5, lambda: os._exit(1)).start()
    return {"message": "Crash agendado em 0.5s"}


@app.post("/chaos/memory-leak", dependencies=[Depends(require_chaos_token)])
def chaos_memory_leak() -> Dict[str, Any]:
    """Increments artificial memory consumption to simulate a memory leak."""
    with _state_lock:
        chaos_events_total.labels(type="memory_leak").inc()
        _state["memory_hog"].append(bytearray(MEMORY_LEAK_CHUNK_BYTES))
        total_bytes = len(_state["memory_hog"]) * MEMORY_LEAK_CHUNK_BYTES
        memory_leak_gauge.set(total_bytes)

    return {"message": "Memory leak incrementado", "total_mb": total_bytes / (1024 * 1024)}


@app.post("/chaos/hang", dependencies=[Depends(require_chaos_token)])
def chaos_hang() -> Dict[str, str]:
    """Sets the application state to hung, causing delays in endpoints."""
    with _state_lock:
        chaos_events_total.labels(type="hang").inc()
        _state["hung"] = True
        app_hung_gauge.set(1)

    return {"message": "App agora está 'hung' (respostas lentas/travadas)"}


@app.post("/chaos/reset", dependencies=[Depends(require_chaos_token)])
def chaos_reset() -> Dict[str, str]:
    """Resets the application state to normal, clearing memory leaks and hung state."""
    with _state_lock:
        chaos_events_total.labels(type="reset").inc()
        _state["memory_hog"] = []
        _state["hung"] = False
        memory_leak_gauge.set(0)
        app_hung_gauge.set(0)

    return {"message": "Estado resetado ao normal"}
