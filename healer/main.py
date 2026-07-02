import time
import requests
import docker
import logging
import json
import os
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
VICTIM_CONTAINER = os.getenv("VICTIM_CONTAINER", "victim-app")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "10"))
MEMORY_LEAK_THRESHOLD = int(os.getenv("MEMORY_LEAK_THRESHOLD", "100000000"))
HUNG_THRESHOLD = int(os.getenv("HUNG_THRESHOLD", "1"))

def query_prometheus(query: str) -> float:
    """Queries Prometheus for the current value of a metric."""
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
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

def restart_container(reason: str) -> None:
    """Restarts the victim-app container and logs the incident."""
    try:
        client = docker.from_env()
        try:
            container = client.containers.get(VICTIM_CONTAINER)
        except docker.errors.NotFound:
            log.error(f"[ERROR] Container '{VICTIM_CONTAINER}' not found.")
            return
        except docker.errors.APIError as e:
            log.error(f"[ERROR] Docker API error when getting container: {e}")
            return
        
        log.warning(f"[ALERT] INCIDENT DETECTED: {reason}")
        log.warning(f"[ACTION] Restarting container '{VICTIM_CONTAINER}'...")
        
        start_time = time.time()
        container.restart(timeout=10)
        duration_seconds = int(time.time() - start_time)
        
        log.info(f"[OK] Container '{VICTIM_CONTAINER}' restarted successfully!")
        log.info(f"[INFO] Incident registered: [{datetime.now()}] {reason}")
        
        # Save incident to JSON
        incident_type = "hung"
        if "memory leak" in reason.lower():
            incident_type = "memory_leak"
        elif "crash" in reason.lower():
            incident_type = "crash"
            
        incident = {
            "timestamp": datetime.now().isoformat(timespec='seconds'),
            "type": incident_type,
            "details": reason,
            "action": f"restarted container {VICTIM_CONTAINER}",
            "status": "resolved",
            "duration_seconds": duration_seconds
        }
        
        file_path = "/app/incidents/incidents.json"
        incidents = []
        
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                    incidents = data.get("incidents", [])
            except json.JSONDecodeError:
                log.error("[ERROR] Failed to parse incidents.json, starting fresh.")
                
        incident["id"] = len(incidents) + 1
        incidents.append(incident)
        
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                json.dump({"incidents": incidents}, f, indent=2)
        except OSError as e:
            log.error(f"[ERROR] Failed to write to {file_path}: {e}")
            
    except docker.errors.DockerException as e:
        log.error(f"[ERROR] Docker connection failed: {e}")
    except Exception as e:
        log.error(f"[ERROR] Unexpected error while restarting container: {e}")

def check_and_heal() -> None:
    """Checks metrics and triggers healing actions if necessary."""
    memory = query_prometheus("app_memory_leak_bytes")
    hung = query_prometheus("app_is_hung")
    
    log.info(f"[INFO] Status — memory: {memory/1_000_000:.1f}MB | hung: {int(hung)}")
    
    if hung >= HUNG_THRESHOLD:
        restart_container(f"App is hung (app_is_hung={int(hung)})")
        return

    if memory >= MEMORY_LEAK_THRESHOLD:
        restart_container(f"Memory leak detected ({memory/1_000_000:.0f}MB >= {MEMORY_LEAK_THRESHOLD/1_000_000:.0f}MB)")
        return

def main() -> None:
    """Main loop for the auto-healer process."""
    log.info("[INFO] Auto-Healer started. Monitoring every %d seconds...", CHECK_INTERVAL)
    log.info("[INFO] Memory Threshold: %dMB", MEMORY_LEAK_THRESHOLD/1_000_000)
    log.info("[INFO] Hung Threshold: %d", HUNG_THRESHOLD)
    
    time.sleep(15)
    
    while True:
        check_and_heal()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
