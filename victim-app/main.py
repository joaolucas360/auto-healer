from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Gauge, Counter
import time
import threading

app = FastAPI(title="Victim App - Auto-Healer Demo")

# ---- Métricas customizadas ----
memory_leak_gauge = Gauge("app_memory_leak_bytes", "Memória artificialmente consumida pelo chaos endpoint")
chaos_events_total = Counter("app_chaos_events_total", "Eventos de caos disparados", ["type"])
app_hung_gauge = Gauge("app_is_hung", "1 se app está em estado de hang simulado, 0 caso contrário")

# ---- Estado interno (simples, em memória) ----
_state = {
    "memory_hog": [],   # lista que vai crescer pra simular leak
    "hung": False,
}

instrumentator = Instrumentator().instrument(app)


@app.on_event("startup")
async def startup():
    instrumentator.expose(app)  # expõe /metrics


@app.get("/health")
def health():
    if _state["hung"]:
        time.sleep(10)  # simula travamento: health check vai demorar/falhar
    return {"status": "ok", "hung": _state["hung"]}


@app.get("/data")
def data():
    if _state["hung"]:
        time.sleep(10)
    return {"message": "dados normais da aplicação", "timestamp": time.time()}


@app.post("/chaos/crash")
def chaos_crash():
    chaos_events_total.labels(type="crash").inc()
    # encerra o processo de forma abrupta (simula crash real)
    threading.Timer(0.5, lambda: __import__("os")._exit(1)).start()
    return {"message": "Crash agendado em 0.5s"}


@app.post("/chaos/memory-leak")
def chaos_memory_leak():
    chaos_events_total.labels(type="memory_leak").inc()
    # adiciona ~50MB por chamada
    _state["memory_hog"].append(bytearray(50 * 1024 * 1024))
    total_bytes = len(_state["memory_hog"]) * 50 * 1024 * 1024
    memory_leak_gauge.set(total_bytes)
    return {"message": "Memory leak incrementado", "total_mb": total_bytes / (1024 * 1024)}


@app.post("/chaos/hang")
def chaos_hang():
    chaos_events_total.labels(type="hang").inc()
    _state["hung"] = True
    app_hung_gauge.set(1)
    return {"message": "App agora está 'hung' (respostas lentas/travadas)"}


@app.post("/chaos/reset")
def chaos_reset():
    chaos_events_total.labels(type="reset").inc()
    _state["memory_hog"] = []
    _state["hung"] = False
    memory_leak_gauge.set(0)
    app_hung_gauge.set(0)
    return {"message": "Estado resetado ao normal"}
