import time
import requests
import docker
import logging
import json
import os
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
VICTIM_CONTAINER = os.getenv("VICTIM_CONTAINER", "victim-app")
INCIDENT_LOG_PATH = Path(os.getenv("INCIDENT_LOG_PATH", "/app/incidents/incidents.json"))


def get_int_env(name: str, default: int, minimum: int = 0) -> int:
    """Reads a positive integer env var and falls back safely."""
    value = os.getenv(name)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        log.warning("[WARN] Invalid %s=%r, using default %s", name, value, default)
        return default

    if parsed < minimum:
        log.warning("[WARN] %s must be >= %s, using default %s", name, minimum, default)
        return default

    return parsed


CHECK_INTERVAL = get_int_env("CHECK_INTERVAL", 10, minimum=1)
MEMORY_LEAK_THRESHOLD = get_int_env("MEMORY_LEAK_THRESHOLD", 100_000_000, minimum=1)
HUNG_THRESHOLD = get_int_env("HUNG_THRESHOLD", 1, minimum=1)
PROMETHEUS_TIMEOUT_SECONDS = get_int_env("PROMETHEUS_TIMEOUT_SECONDS", 5, minimum=1)
RESTART_COOLDOWN_SECONDS = get_int_env("RESTART_COOLDOWN_SECONDS", 60, minimum=0)
STARTUP_DELAY_SECONDS = get_int_env("STARTUP_DELAY_SECONDS", 15, minimum=0)

session = requests.Session()
last_restart_at = 0.0


def query_prometheus(query: str) -> float:
    """Queries Prometheus for the current value of a metric."""
    try:
        response = session.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=PROMETHEUS_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            log.error("[ERROR] Prometheus query failed: %s", data)
            return 0.0

        results = data.get("data", {}).get("result", [])
        if results:
            return float(results[0]["value"][1])
        return 0.0
    except requests.RequestException as e:
        log.error(f"[ERROR] Failed to query Prometheus: {e}")
        return 0.0
    except Exception as e:
        log.error(f"[ERROR] Unexpected error querying Prometheus: {e}")
        return 0.0


def load_incidents() -> list[dict]:
    """Loads the current incident ledger."""
    if not INCIDENT_LOG_PATH.exists():
        return []

    try:
        with INCIDENT_LOG_PATH.open("r") as f:
            data = json.load(f)
    except JSONDecodeError:
        log.error("[ERROR] Failed to parse %s, starting fresh.", INCIDENT_LOG_PATH)
        return []
    except OSError as e:
        log.error("[ERROR] Failed to read %s: %s", INCIDENT_LOG_PATH, e)
        return []

    if not isinstance(data, dict):
        log.error("[ERROR] Invalid incident ledger root, starting fresh.")
        return []

    incidents = data.get("incidents", [])
    if not isinstance(incidents, list):
        log.error("[ERROR] Invalid incident ledger format, starting fresh.")
        return []

    return incidents


def save_incident(incident_type: str, reason: str, duration_seconds: int) -> bool:
    """Appends an incident to the JSON ledger using an atomic replace."""
    incidents = load_incidents()
    incident = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "type": incident_type,
        "details": reason,
        "action": f"restarted container {VICTIM_CONTAINER}",
        "status": "resolved",
        "duration_seconds": duration_seconds,
        "id": len(incidents) + 1,
    }

    incidents.append(incident)
    INCIDENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = INCIDENT_LOG_PATH.with_suffix(".tmp")

    try:
        with tmp_path.open("w") as f:
            json.dump({"incidents": incidents}, f, indent=2)
        tmp_path.replace(INCIDENT_LOG_PATH)
        return True
    except OSError as e:
        log.error("[ERROR] Failed to write to %s: %s", INCIDENT_LOG_PATH, e)
        return False


def restart_container(incident_type: str, reason: str) -> bool:
    """Restarts the victim-app container and logs the incident."""
    try:
        client = docker.from_env()
        try:
            container = client.containers.get(VICTIM_CONTAINER)
        except docker.errors.NotFound:
            log.error(f"[ERROR] Container '{VICTIM_CONTAINER}' not found.")
            return False
        except docker.errors.APIError as e:
            log.error(f"[ERROR] Docker API error when getting container: {e}")
            return False

        log.warning(f"[ALERT] INCIDENT DETECTED: {reason}")
        log.warning(f"[ACTION] Restarting container '{VICTIM_CONTAINER}'...")

        start_time = time.time()
        container.restart(timeout=10)
        duration_seconds = int(time.time() - start_time)

        log.info(f"[OK] Container '{VICTIM_CONTAINER}' restarted successfully!")

        try:
            incident_saved = save_incident(incident_type, reason, duration_seconds)
        except Exception as e:
            incident_saved = False
            log.error("[ERROR] Unexpected error while saving incident: %s", e)

        if incident_saved:
            log.info(f"[INFO] Incident registered: [{datetime.now()}] {reason}")
        else:
            log.error(
                "[ERROR] Container restart succeeded and cooldown will be applied, "
                "but incident persistence failed."
            )
        return True

    except docker.errors.DockerException as e:
        log.error(f"[ERROR] Docker connection failed: {e}")
    except Exception as e:
        log.error(f"[ERROR] Unexpected error while restarting container: {e}")
    return False


def check_and_heal() -> None:
    """Checks metrics and triggers healing actions if necessary."""
    global last_restart_at

    memory = query_prometheus("app_memory_leak_bytes")
    hung = query_prometheus("app_is_hung")

    log.info(f"[INFO] Status — memory: {memory/1_000_000:.1f}MB | hung: {int(hung)}")

    if time.time() - last_restart_at < RESTART_COOLDOWN_SECONDS:
        log.info("[INFO] Restart cooldown active, skipping remediation.")
        return

    if hung >= HUNG_THRESHOLD:
        if restart_container("hung", f"App is hung (app_is_hung={int(hung)})"):
            last_restart_at = time.time()
        return

    if memory >= MEMORY_LEAK_THRESHOLD:
        reason = (
            f"Memory leak detected "
            f"({memory/1_000_000:.0f}MB >= {MEMORY_LEAK_THRESHOLD/1_000_000:.0f}MB)"
        )
        if restart_container("memory_leak", reason):
            last_restart_at = time.time()
        return


def main() -> None:
    """Main loop for the auto-healer process."""
    log.info("[INFO] Auto-Healer started. Monitoring every %d seconds...", CHECK_INTERVAL)
    log.info("[INFO] Memory Threshold: %dMB", MEMORY_LEAK_THRESHOLD/1_000_000)
    log.info("[INFO] Hung Threshold: %d", HUNG_THRESHOLD)
    log.info("[INFO] Restart cooldown: %ds", RESTART_COOLDOWN_SECONDS)

    time.sleep(STARTUP_DELAY_SECONDS)

    while True:
        check_and_heal()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
