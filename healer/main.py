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

PROMETHEUS_URL = "http://prometheus:9090"
VICTIM_CONTAINER = "victim-app"
CHECK_INTERVAL = 10  # segundos entre cada verificação

# thresholds que disparam a remediação
MEMORY_LEAK_THRESHOLD = 100_000_000  # 100MB
HUNG_THRESHOLD = 1                   # qualquer valor >= 1 = travado

def query_prometheus(query):
    """Pergunta pro Prometheus o valor atual de uma métrica."""
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5
        )
        data = response.json()
        results = data.get("data", {}).get("result", [])
        if results:
            return float(results[0]["value"][1])
        return 0.0
    except Exception as e:
        log.error(f"Erro ao consultar Prometheus: {e}")
        return 0.0

def restart_container(reason):
    """Reinicia o container da victim-app e registra o incidente."""
    try:
        client = docker.from_env()
        container = client.containers.get(VICTIM_CONTAINER)
        
        log.warning(f"🚨 INCIDENTE DETECTADO: {reason}")
        log.warning(f"🔄 Reiniciando container '{VICTIM_CONTAINER}'...")
        
        start_time = time.time()
        container.restart(timeout=10)
        duration_seconds = int(time.time() - start_time)
        
        log.info(f"✅ Container '{VICTIM_CONTAINER}' reiniciado com sucesso!")
        log.info(f"📋 Incidente registrado: [{datetime.now()}] {reason}")
        
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
                pass
                
        incident["id"] = len(incidents) + 1
        incidents.append(incident)
        
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            json.dump({"incidents": incidents}, f, indent=2)
            
    except Exception as e:
        log.error(f"Erro ao reiniciar container: {e}")

def check_and_heal():
    """Verifica métricas e age se necessário."""
    memory = query_prometheus("app_memory_leak_bytes")
    hung = query_prometheus("app_is_hung")
    
    log.info(f"📊 Status — memória: {memory/1_000_000:.1f}MB | hung: {int(hung)}")
    
    if hung >= HUNG_THRESHOLD:
        restart_container(f"App travado (app_is_hung={int(hung)})")
        return

    if memory >= MEMORY_LEAK_THRESHOLD:
        restart_container(f"Memory leak detectado ({memory/1_000_000:.0f}MB >= {MEMORY_LEAK_THRESHOLD/1_000_000:.0f}MB)")
        return

def main():
    log.info("🏥 Auto-Healer iniciado. Monitorando a cada 10 segundos...")
    log.info(f"   Threshold memória: {MEMORY_LEAK_THRESHOLD/1_000_000:.0f}MB")
    log.info(f"   Threshold hung: {HUNG_THRESHOLD}")
    
    # espera o Prometheus estar pronto antes de começar
    time.sleep(15)
    
    while True:
        check_and_heal()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
