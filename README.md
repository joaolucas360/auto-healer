# Auto-Healer

[![CI](https://github.com/joaolucas360/auto-healer/actions/workflows/ci.yml/badge.svg)](https://github.com/joaolucas360/auto-healer/actions)
![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-green.svg)

Demo local de auto-healing com observabilidade. O projeto simula uma API que pode entrar em falha controlada, expoe metricas para o Prometheus, mostra o comportamento no Grafana e usa um servico separado para reiniciar a aplicacao quando uma condicao critica e detectada.

A ideia principal e mostrar o ciclo completo:

1. a aplicacao expoe metricas;
2. o Prometheus coleta essas metricas;
3. o Grafana exibe o estado da aplicacao;
4. o `healer` consulta o Prometheus;
5. quando existe falha, o `healer` reinicia o container da `victim-app`;
6. o incidente e registrado em um ledger JSON.

Este projeto foi feito para demonstracao local. Ele nao deve ser usado como modelo de seguranca para producao sem as adaptacoes descritas na secao de seguranca.

---

## Arquitetura

```text
+------------+               +------------+
| victim-app |  scrape 5s    | Prometheus |
|  :8000     | ------------> |   :9090    |
+------------+               +------------+
      ^                            |
      | restart                    | query PromQL
      |                            v
+------------+               +------------+
| Docker API | <------------ |   Healer   |
| docker.sock|   restart     |  Python    |
+------------+               +------------+
                                   |
                                   v
                           incidents.json
```

## Servicos

| Servico | Porta | Imagem | Funcao |
|---|---:|---|---|
| `victim-app` | `8000` | `python:3.12-slim` | API FastAPI, endpoints de chaos e metricas Prometheus. |
| `prometheus` | `9090` | `prom/prometheus:latest` | Coleta metricas da `victim-app` a cada 5 segundos. |
| `grafana` | `3000` | `grafana/grafana:11.6.0` | Dashboard visual provisionado automaticamente. |
| `healer` | interno | `python:3.12-slim` | Consulta o Prometheus e reinicia a `victim-app` quando necessario. |

---

## Como Funciona

O fluxo de recuperacao e baseado em metricas, nao em chamadas diretas para a API da aplicacao.

1. Um endpoint `/chaos/*` simula uma falha, como memory leak ou travamento.
2. A `victim-app` atualiza metricas customizadas em `/metrics`.
3. O Prometheus coleta essas metricas.
4. O `healer` executa consultas PromQL periodicamente.
5. Se `app_is_hung >= 1` ou `app_memory_leak_bytes` ultrapassar o limite configurado, o `healer` reinicia o container `victim-app`.
6. Depois do restart, o `healer` registra o incidente e aplica cooldown para evitar loop de reinicios.

---

## Como Rodar Localmente

### Pre-requisitos

- Docker
- Docker Compose

No macOS com Colima, garanta que o Docker esteja ativo antes de subir a stack.

### 1. Clonar o repositorio

```bash
git clone https://github.com/joaolucas360/auto-healer.git
cd auto-healer
```

### 2. Criar o arquivo `.env`

```bash
cp .env.example .env
```

Para uma demo mais realista, configure um token:

```env
CHAOS_TOKEN=demo-token
```

Com `CHAOS_TOKEN` preenchido, todos os endpoints `/chaos/*` exigem o header:

```http
x-chaos-token: demo-token
```

Se `CHAOS_TOKEN` estiver vazio, os endpoints de chaos ficam sem autenticacao. Isso facilita uma demo local rapida, mas nao deve ser usado em maquina compartilhada ou ambiente exposto.

### 3. Ajustar o GID do Docker socket

O `healer` roda como usuario nao-root, mas precisa acessar `/var/run/docker.sock`.

No Linux, descubra o GID do socket e grave no `.env`:

```bash
echo "DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)" >> .env
```

No Docker Desktop ou Colima no macOS, o socket vem de uma VM Linux. O valor padrao `DOCKER_GID=991` costuma funcionar. Se o `healer` nao conseguir acessar o socket, inspecione o grupo do socket dentro do container e ajuste `DOCKER_GID`.

### 4. Subir a stack

```bash
docker compose up --build -d
```

### 5. Conferir os containers

```bash
docker compose ps
```

O esperado e ver `victim-app`, `prometheus`, `grafana` e `healer` em execucao. A `victim-app` e o `healer` devem aparecer como `healthy`.

---

## URLs

| Servico | URL |
|---|---|
| Swagger / OpenAPI | http://localhost:8000/docs |
| Healthcheck | http://localhost:8000/health |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Dashboard principal | http://localhost:3000/d/auto-healer-live/auto-healer-live-demo |

Credenciais padrao do Grafana:

```text
usuario: admin
senha: admin
```

O Grafana ja sobe com datasource Prometheus e dashboard `Auto-Healer Live Demo` provisionados automaticamente.

---

## Simulando Incidentes

Carregue o `.env` no shell antes dos comandos:

```bash
set -a
source .env
set +a
```

### Memory leak

```bash
curl -X POST http://localhost:8000/chaos/memory-leak \
  -H "x-chaos-token: ${CHAOS_TOKEN}"
```

Cada chamada aloca cerca de 50 MB artificiais e atualiza `app_memory_leak_bytes`.

Para passar do limite padrao de 100 MB e acionar o healer, execute duas vezes:

```bash
curl -X POST http://localhost:8000/chaos/memory-leak \
  -H "x-chaos-token: ${CHAOS_TOKEN}"

curl -X POST http://localhost:8000/chaos/memory-leak \
  -H "x-chaos-token: ${CHAOS_TOKEN}"
```

### Travamento da aplicacao

```bash
curl -X POST http://localhost:8000/chaos/hang \
  -H "x-chaos-token: ${CHAOS_TOKEN}"
```

Esse comando coloca `app_is_hung` em `1`. O healer detecta a condicao pelo Prometheus e reinicia a `victim-app`.

### Reset manual

```bash
curl -X POST http://localhost:8000/chaos/reset \
  -H "x-chaos-token: ${CHAOS_TOKEN}"
```

### Crash do processo

```bash
curl -X POST http://localhost:8000/chaos/crash \
  -H "x-chaos-token: ${CHAOS_TOKEN}"
```

Esse endpoint encerra o processo da API. O Docker Compose reinicia a aplicacao por causa da politica `unless-stopped`.

---

## Metricas Principais

Consultas uteis no Prometheus:

```promql
app_is_hung
```

```promql
app_memory_leak_bytes
```

```promql
app_chaos_events_total
```

```promql
up{job="victim-app"}
```

Metricas customizadas:

| Metrica | Descricao |
|---|---|
| `app_is_hung` | `1` quando a aplicacao esta em estado travado, `0` quando esta normal. |
| `app_memory_leak_bytes` | Quantidade de memoria artificialmente alocada pelo endpoint de memory leak. |
| `app_chaos_events_total` | Contador de eventos de chaos por tipo. |

---

## Historico de Incidentes

Cada acao de recuperacao registra um item em:

```text
incidents/incidents.json
```

Exemplo:

```json
{
  "incidents": [
    {
      "timestamp": "2026-07-16T02:53:59",
      "type": "hung",
      "details": "App is hung (app_is_hung=1)",
      "action": "restarted container victim-app",
      "status": "resolved",
      "duration_seconds": 1,
      "id": 4
    }
  ]
}
```

Esse arquivo funciona como ledger simples para a demo local. Em producao, o ideal seria gravar esses eventos em storage centralizado, como banco de dados, Loki, OpenSearch, S3 ou uma ferramenta de incidentes.

---

## CI

O workflow em `.github/workflows/ci.yml` roda em push e pull request para `main`.

Ele valida:

- testes da `victim-app`;
- testes do `healer`;
- parsing de YAML;
- `py_compile`;
- configuracao do Docker Compose.

Os testes cobrem autenticacao dos endpoints de chaos, validacao de variaveis de ambiente, consistencia de metricas, consultas ao Prometheus, cooldown, restart mockado e resiliencia do ledger de incidentes.

---

## Consideracoes de Seguranca

Este projeto monta `/var/run/docker.sock` dentro do container `healer`.

Isso e perigoso em producao. Quem tem acesso a esse socket normalmente consegue controlar o Docker host: listar containers, criar containers privilegiados, montar volumes e alterar servicos.

Nesta demo local, o risco e aceito para manter a arquitetura simples e mostrar o loop completo de deteccao e remediacao. Mesmo assim, o `healer` roda como usuario nao-root e recebe acesso ao socket por `group_add`, usando `DOCKER_GID`.

Para um ambiente real, eu mudaria essa parte. Alternativas melhores:

- Kubernetes com `livenessProbe`, `readinessProbe` e restart controlado pelo orquestrador;
- Docker Socket Proxy com permissoes minimas;
- API interna de remediacao com autorizacao forte;
- ECS, Nomad, Docker Swarm ou outro orquestrador;
- secrets gerenciados por Vault, AWS Secrets Manager, GCP Secret Manager ou equivalente;
- logs e incidentes em storage centralizado;
- limites de restart, backoff e alerta humano depois de muitas falhas.

---

## Como Eu Apresentaria Este Projeto

1. Abrir Grafana, Swagger e Prometheus.
2. Mostrar a stack saudavel com `docker compose ps`.
3. Mostrar que `/chaos/*` exige `x-chaos-token`.
4. Executar `/chaos/memory-leak` uma vez e ver o grafico subir.
5. Executar `/chaos/memory-leak` de novo para passar do limite e acionar restart.
6. Mostrar logs do `healer`.
7. Executar `/chaos/hang` e observar o pico em `app_is_hung`.
8. Mostrar `incidents/incidents.json`.
9. Explicar que, em producao, o restart deveria ser responsabilidade de um orquestrador ou API de remediacao com permissoes minimas.

---

## Tecnologias

| Tecnologia | Uso |
|---|---|
| Python | API e daemon de healing |
| FastAPI | API HTTP, Swagger e endpoints de chaos |
| Prometheus | Coleta e consulta de metricas |
| Grafana | Visualizacao da demo |
| Docker Compose | Orquestracao local |
| GitHub Actions | CI |
