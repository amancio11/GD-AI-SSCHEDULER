# MES Production Scheduler

Sistema di schedulazione intelligente per il montaggio di macchine industriali complesse, con integrazione mock SAP Digital Manufacturing.

- Motore di scheduling: **OR-Tools CP-SAT** (ottimizzazione a vincoli)
- AI: **Anthropic Claude Sonnet 4.6**
- Task asincroni: **Celery 5 + Redis 7**
- Database: **PostgreSQL 16**

---

## Stack tecnologico

| Layer | Tecnologia |
|---|---|
| Backend | Python 3.11+, FastAPI, OR-Tools CP-SAT |
| AI | Anthropic Claude Sonnet 4.6 |
| Task queue | Celery 5 + Redis 7 |
| Database | PostgreSQL 16 + SQLAlchemy 2 async |
| Frontend | React 18 + TypeScript, Vite 5, Tailwind CSS, Zustand |
| Export | reportlab (PDF), CSV, JSON-SAP |

---

## Cos'è Celery e perché serve

**Celery** è un sistema di code di task asincroni. Nel MES Scheduler viene usato per operazioni che richiedono troppo tempo per una risposta HTTP sincrona (il browser si bloccherebbe):

| Task Celery | Quando si attiva | Cosa fa |
|---|---|---|
| `run_schedule` | Click su "Schedula" nello Scenario Manager | Costruisce il modello CP-SAT, lo risolve (fino a 60 sec), salva le `schedule_entries` nel DB, notifica il frontend via WebSocket |
| `analyze_proactive_after_schedule` | Automaticamente dopo ogni scheduling | Analizza il piano con regole + Claude: cerca operatori sovraccarichi, componenti a rischio, finestre critiche |
| `reschedule_on_delay` | Creazione di un `DelayEvent` | Ri-esegue il solver mantenendo fisse le operazioni già completate/in corso |

**Redis** è il broker della coda: Celery pubblica i task su Redis, i worker li prelevano e li eseguono. **Senza Redis attivo, i task vengono accodati ma non eseguiti → lo scheduling non parte.**

---

## OPZIONE 1 — Avvio con Docker (più semplice)

Docker avvia automaticamente tutti i componenti (backend, frontend, postgres, redis, celery worker) in container isolati, senza installare nulla sul PC host.

### Prerequisiti

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) ≥ 24 installato e avviato
- Docker Compose v2 (incluso in Docker Desktop)

### Passaggi

```bash
# 1. Entra nella cartella del progetto
cd gd-scheduler

# 2. Crea il file .env con la tua API key Anthropic
cp .env.example .env
# Apri .env e imposta:
# ANTHROPIC_API_KEY=sk-ant-api03-...

# 3. Costruisci le immagini e avvia tutti i container
docker compose up --build -d

# 4. Verifica che tutti i container siano "Up"
docker compose ps

# 5. Esegui le migrazioni del database (solo al primo avvio)
docker compose exec backend python -m alembic upgrade head

# 6. Popola i dati mock TURBOPRESS-X500 (solo al primo avvio, idempotente)
docker compose exec backend python -m app.db.seed

# 7. Apri l'applicazione
# Frontend:    http://localhost:5173
# Backend API: http://localhost:8000/docs
```

### Comandi Docker utili

```bash
# Log in tempo reale di tutti i servizi
docker compose logs -f

# Log solo del backend o del worker Celery
docker compose logs -f backend
docker compose logs -f celery

# Fermare tutto (i dati nel DB sono conservati)
docker compose stop

# Fermare e cancellare tutto, incluso il volume del DB
docker compose down -v

# Aprire una shell nel container backend
docker compose exec backend bash

# Rieseguire il seed dopo modifiche
docker compose exec backend python -m app.db.seed
```

---

## OPZIONE 2 — Avvio in locale senza Docker

Utile per sviluppo con hot-reload e debug. Questo è il setup verificato e funzionante sulla macchina di sviluppo (Windows, PC aziendale senza admin).

### Setup una-tantum — installazione componenti

#### Python 3.11+

Scarica da [python.org](https://python.org). Durante l'installazione seleziona **"Add Python to PATH"**.

```powershell
python --version  # deve rispondere Python 3.11.x o superiore
```

#### Node.js 18+

Scarica da [nodejs.org](https://nodejs.org). Scegli la versione LTS.

```powershell
node --version   # deve rispondere v18.x o superiore
```

#### PostgreSQL 16 — versione portable (no admin, no installer)

Non richiede privilegi di amministratore. Scarica il binario ZIP da EnterpriseDB:

```powershell
# Scarica PostgreSQL 16 portable (~300 MB)
$pgZip = "$env:USERPROFILE\Downloads\pgsql-16.zip"
$pgDir = "$env:USERPROFILE\Downloads\pgsql"
$pgData = "$env:USERPROFILE\Downloads\pgsql-data"

Invoke-WebRequest -Uri "https://get.enterprisedb.com/postgresql/postgresql-16.9-1-windows-x64-binaries.zip" `
    -OutFile $pgZip -UseBasicParsing

Expand-Archive -Path $pgZip -DestinationPath "$env:USERPROFILE\Downloads\"

# Inizializza il cluster (crea i file di sistema del DB)
& "$pgDir\bin\initdb.exe" -D $pgData -U postgres -E UTF8 --locale=en_US.UTF-8

# Avvia il server
& "$pgDir\bin\pg_ctl.exe" -D $pgData -l "$pgData\postgres.log" start

# Crea utente e database per il progetto
& "$pgDir\bin\psql.exe" -U postgres -c "CREATE USER scheduler WITH PASSWORD 'scheduler';"
& "$pgDir\bin\psql.exe" -U postgres -c "CREATE DATABASE scheduler OWNER scheduler;"
```

**Per riavviare PostgreSQL dopo un riavvio del PC:**
```powershell
$pgDir = "$env:USERPROFILE\Downloads\pgsql"
$pgData = "$env:USERPROFILE\Downloads\pgsql-data"
& "$pgDir\bin\pg_ctl.exe" -D $pgData -l "$pgData\postgres.log" start
```

**Per fermare PostgreSQL:**
```powershell
& "$env:USERPROFILE\Downloads\pgsql\bin\pg_ctl.exe" -D "$env:USERPROFILE\Downloads\pgsql-data" stop
```

#### Redis 7 — versione portable (no admin, no installer)

Usa il port Windows di Redis mantenuto da tporadowski (stabile, compatibile con Celery):

```powershell
# Scarica Redis portable per Windows (~5 MB)
$redisZip = "$env:USERPROFILE\Downloads\redis-portable.zip"
$redisDir = "$env:USERPROFILE\Downloads\redis"

Invoke-WebRequest -Uri "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip" `
    -OutFile $redisZip -UseBasicParsing

Expand-Archive -Path $redisZip -DestinationPath $redisDir

# Avvia Redis in background
Start-Process -FilePath "$redisDir\redis-server.exe" `
    -ArgumentList "$redisDir\redis.windows.conf" `
    -WindowStyle Hidden

# Verifica che funzioni (deve rispondere PONG)
& "$redisDir\redis-cli.exe" ping
```

**Per riavviare Redis dopo un riavvio del PC:**
```powershell
Start-Process -FilePath "$env:USERPROFILE\Downloads\redis\redis-server.exe" `
    -ArgumentList "$env:USERPROFILE\Downloads\redis\redis.windows.conf" `
    -WindowStyle Hidden
```

---

### Avvio del progetto (dopo aver installato i componenti sopra)

#### Prima volta — setup completo

```powershell
# Dalla root del progetto
cd gd-scheduler

# 1. Configura le variabili d'ambiente backend
#    (copia e poi apri il file per impostare ANTHROPIC_API_KEY)
Copy-Item .env.example backend\.env
# Apri backend\.env e imposta ANTHROPIC_API_KEY=sk-ant-...

# 2. Configura le variabili frontend
@"
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
"@ | Set-Content frontend\.env

# 3. Installa dipendenze Python nel virtualenv
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # attiva il virtualenv
python.exe -m pip install -r requirements.txt

# 4. Esegui le migrazioni del database
python -m alembic upgrade head

# 5. Popola i dati mock TURBOPRESS-X500
python -m app.db.seed

# 6. Installa dipendenze frontend
cd ..\frontend
npm install
```

#### Avvio giornaliero — 4 terminali

Apri 4 terminali PowerShell separati (o tab nel Windows Terminal):

**Terminale 1 — PostgreSQL** (solo se non già avviato):
```powershell
$pgDir = "$env:USERPROFILE\Downloads\pgsql"
& "$pgDir\bin\pg_ctl.exe" -D "$env:USERPROFILE\Downloads\pgsql-data" -l "$env:USERPROFILE\Downloads\pgsql-data\postgres.log" start
```

**Terminale 2 — Redis** (solo se non già avviato):
```powershell
Start-Process -FilePath "$env:USERPROFILE\Downloads\redis\redis-server.exe" `
    -ArgumentList "$env:USERPROFILE\Downloads\redis\redis.windows.conf" -WindowStyle Hidden
# Verifica:
& "$env:USERPROFILE\Downloads\redis\redis-cli.exe" ping  # deve rispondere PONG
```

**Terminale 3 — Backend FastAPI** (porta 8000):
```powershell
cd backend
$env:DATABASE_URL = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler"
$env:REDIS_URL    = "redis://localhost:6379/0"
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # la tua chiave
$env:ENVIRONMENT  = "development"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir .
```

**Terminale 4 — Celery Worker**:
```powershell
cd backend
$env:DATABASE_URL = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler"
$env:REDIS_URL    = "redis://localhost:6379/0"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:ENVIRONMENT  = "development"

# --pool=solo è NECESSARIO su Windows: Windows non ha fork() POSIX
# Su Linux/Mac usare: --pool=prefork --concurrency=4
python -m celery -A celery_worker.celery_app worker --loglevel=info --pool=solo
```

**Terminale 5 — Frontend Vite** (porta 5173):
```powershell
cd frontend
npm run dev
```

#### Verifica che tutto funzioni

```powershell
# 1. PostgreSQL
& "$env:USERPROFILE\Downloads\pgsql\bin\pg_ctl.exe" -D "$env:USERPROFILE\Downloads\pgsql-data" status
# → "pg_ctl: server is running"

# 2. Redis
& "$env:USERPROFILE\Downloads\redis\redis-cli.exe" ping
# → PONG

# 3. Backend API
Invoke-RestMethod http://localhost:8000/health
# → { "status": "ok" }

# 4. Apri nel browser
Start-Process "http://localhost:5173"       # frontend
Start-Process "http://localhost:8000/docs"  # Swagger UI
```

---

## Flusso operativo — Come usare il sistema

1. Apri il **frontend** → http://localhost:5173
2. Vai su **Scenario Manager** → crea un nuovo scenario scegliendo obiettivo (es. `FINISH_BY_DATE`) e data target
3. Clicca **Crea e Schedula** → il task viene inviato a Celery che avvia il solver CP-SAT
4. Il badge WebSocket in header si aggiorna quando lo scheduling è completato (5–60 sec)
5. Vai su **Gantt View** → visualizza il piano per operatore o per ordine di produzione
6. Vai su **AI Assistant** → chiedi spiegazioni, analisi ritardi, confronto scenari
7. Usa **Export** → scarica il piano in CSV (Excel), JSON-SAP o PDF

---

## Variabili d'ambiente

| Variabile | Descrizione | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (asyncpg) | `postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler` |
| `REDIS_URL` | Redis broker URL | `redis://localhost:6379/0` |
| `ANTHROPIC_API_KEY` | Chiave API Anthropic — [ottienila qui](https://console.anthropic.com/) | — |
| `CPSAT_TIMEOUT_SECONDS` | Timeout massimo del solver CP-SAT | `60` |
| `MIN_OP_DURATION_MINUTES` | Durata minima di un'operazione schedulata | `30` |
| `ENVIRONMENT` | `development` o `production` | `development` |
| `VITE_API_URL` | URL del backend visto dal browser | `http://localhost:8000` |
| `VITE_WS_URL` | URL WebSocket visto dal browser | `ws://localhost:8000` |

---

## Struttura del progetto

```
gd-scheduler/
├── backend/
│   ├── app/
│   │   ├── api/routes/        # FastAPI routers (orders, schedule, ai, export, …)
│   │   ├── core/
│   │   │   ├── ai/            # Claude client, prompt builder, context extractor
│   │   │   └── scheduler/     # CP-SAT engine, DAG builder, shift preprocessor
│   │   ├── models/            # SQLAlchemy models (19 tabelle)
│   │   ├── schemas/           # Pydantic v2 schemas
│   │   └── db/                # Session async, seed script TURBOPRESS-X500
│   ├── alembic/               # Migrazioni database
│   ├── celery_worker.py       # Configurazione app Celery
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/             # Dashboard, Gantt, BOM, Calendar, AI, Export, …
│       ├── components/        # Layout, Gantt, BOM, AI sidebar
│       ├── api/               # Axios client + React Query hooks
│       └── store/             # Zustand stores (schedule, operator, ai, ui)
├── docker-compose.yml
├── start-local.ps1            # Script avvio locale Windows (automatizzato)
└── .env.example
```

---

## Integrazioni future

### Collegare SAP reale

Il sistema usa attualmente dati mock (seed TURBOPRESS-X500). Per collegare un SAP reale:

1. Sostituisci `backend/app/db/seed.py` con un connettore SAP-RFC o OData.
2. Mappa i campi SAP → modelli `production_orders` e `z_orders_link`.
3. Imposta un job periodico (Celery beat) per sincronizzare gli ordini.
4. Aggiorna `DATABASE_URL` puntando a un PostgreSQL di produzione.
5. Configura SSL/TLS e credenziali sicure tramite secrets manager.

### Moduli aggiuntivi

- **SAP DM Integration**: webhook su eventi di avanzamento operazioni in tempo reale.
- **ERP Feedback**: esporta schedule confermato via `GET /api/export/scenario/{id}/json-sap`.
- **Multi-plant**: estendi `workcenters` con `plant_code` e replica il modello per più stabilimenti.
- **Celery Beat**: aggiungi task periodici (sincronizzazione SAP, pulizia sessioni AI scadute).


---

## Stack tecnologico

| Layer | Tecnologia |
|---|---|
| Backend | Python 3.11+, FastAPI, OR-Tools CP-SAT |
| AI | Anthropic Claude Sonnet 4.6 |
| Task queue | Celery 5 + Redis 7 |
| Database | PostgreSQL 16 + SQLAlchemy 2 async |
| Frontend | React 18 + TypeScript, Vite 5, Tailwind CSS, Zustand |
| Export | reportlab (PDF), CSV, JSON-SAP |

---

## Cos'è Celery e perché serve

**Celery** è un sistema di code di task asincroni. Nel MES Scheduler viene usato per operazioni che richiedono troppo tempo per una risposta HTTP sincrona:

| Task Celery | Quando si attiva | Cosa fa |
|---|---|---|
| `run_schedule` | Click su "Schedula" nello Scenario Manager | Costruisce il modello CP-SAT, lo risolve (fino a 60 sec), salva le `schedule_entries` nel DB, notifica il frontend via WebSocket |
| `analyze_proactive_after_schedule` | Automaticamente dopo ogni scheduling | Analizza il piano appena creato con regole + Claude: cerca operatori sovraccarichi, componenti a rischio, finestre critiche |
| `reschedule_on_delay` | Creazione di un `DelayEvent` | Ri-esegue il solver mantenendo fisse le operazioni già completate/in corso |

**Redis** è il broker: Celery mette i task in coda su Redis, i worker li prelevano ed eseguono. Senza Redis attivo, i task vengono accodati ma non eseguiti → lo scheduling non parte.

---

## Avvio con Docker (raccomandato)

Docker avvia automaticamente tutti e 5 i componenti (backend, frontend, postgres, redis, celery worker) in container isolati.

### Prerequisiti

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) ≥ 24 installato e avviato
- Docker Compose v2 (incluso in Docker Desktop)

### Passaggi

```bash
# 1. Entra nella cartella del progetto
cd gd-scheduler

# 2. Copia le variabili d'ambiente e imposta la tua API key Anthropic
cp .env.example .env
# Apri .env e sostituisci "sk-ant-..." con la tua chiave reale:
# ANTHROPIC_API_KEY=sk-ant-api03-...

# 3. Costruisci le immagini e avvia tutti i container in background
docker compose up --build -d

# 4. Verifica che tutti i container siano "Up"
docker compose ps

# 5. Esegui le migrazioni del database (solo al primo avvio)
docker compose exec backend alembic upgrade head

# 6. Popola i dati mock TURBOPRESS-X500 (solo al primo avvio, idempotente)
docker compose exec backend python -m app.db.seed

# 7. Apri l'applicazione
# Frontend:   http://localhost:5173
# Backend API: http://localhost:8000/docs
```

### Comandi Docker utili

```bash
# Vedere i log in tempo reale di tutti i servizi
docker compose logs -f

# Vedere i log solo del backend
docker compose logs -f backend

# Vedere i log del worker Celery
docker compose logs -f celery

# Fermare tutto (mantiene i dati nel DB)
docker compose stop

# Fermare e cancellare tutto (incluso il DB!)
docker compose down -v

# Rieseguire il seed dopo modifiche
docker compose exec backend python -m app.db.seed

# Aprire una shell nel container backend
docker compose exec backend bash
```

---

## Avvio in locale senza Docker

Utile per sviluppo con hot-reload e debug. Richiede PostgreSQL e Redis installati sul proprio sistema (o in Docker solo per questi due servizi).

### Prerequisiti

| Componente | Versione | Come installare |
|---|---|---|
| Python | 3.11+ | [python.org](https://python.org) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| PostgreSQL | 16 | Vedi sezione sotto |
| Redis | 7 | Vedi sezione sotto |

### 1. Avviare PostgreSQL in locale

PostgreSQL è il database principale. Deve essere raggiungibile su `localhost:5432`.

**Opzione A — Docker (più semplice, nessuna installazione):**
```bash
# Avvia solo PostgreSQL in Docker, esponendo la porta 5432 all'host
docker run -d \
  --name scheduler-postgres \
  -e POSTGRES_USER=scheduler \
  -e POSTGRES_PASSWORD=scheduler \
  -e POSTGRES_DB=scheduler \
  -p 5432:5432 \
  postgres:16
```

**Opzione B — PostgreSQL installato su Windows:**
```powershell
# Verifica se il servizio esiste e avvialo
Get-Service | Where-Object { $_.Name -like "*postgresql*" }
Start-Service "postgresql-x64-16"   # il nome può variare, es. postgresql-16

# Crea l'utente e il database (esegui nel psql o in pgAdmin):
# CREATE USER scheduler WITH PASSWORD 'scheduler';
# CREATE DATABASE scheduler OWNER scheduler;
```

**Opzione C — Installer ufficiale PostgreSQL (raccomandato su Windows):**

Scarica ed esegui l'installer da [enterprisedb.com/downloads/postgres-postgresql-downloads](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads).

Durante l'installazione:
- Scegli versione **16.x**
- Password superuser: scegli una password (non serve per lo scheduler)
- Porta: lascia `5432`
- Al termine, apri **pgAdmin 4** oppure **psql** (installati insieme) e crea utente e DB:

```sql
-- Esegui in psql (Start → pgAdmin → Query Tool, oppure "SQL Shell (psql)")
CREATE USER scheduler WITH PASSWORD 'scheduler';
CREATE DATABASE scheduler OWNER scheduler;
```

**Nota:** `winget install PostgreSQL.PostgreSQL.16` funziona **solo da PowerShell**, non da Git Bash/MINGW64.

### 2. Avviare Redis in locale

Redis è usato come broker per Celery. Deve essere raggiungibile su `localhost:6379`.

**Opzione A — Docker (raccomandato su Windows):**
```bash
docker run -d \
  --name scheduler-redis \
  -p 6379:6379 \
  redis:7
```

**Opzione B — Redis via winget (Windows):**
```powershell
winget install Redis.Redis
# Redis si avvia automaticamente come servizio Windows
# Per avviarlo/fermarlo manualmente:
Start-Service Redis
Stop-Service Redis
```

**Verifica che Redis funzioni:**
```bash
redis-cli ping
# Risposta attesa: PONG
```

### 3. Configurare le variabili d'ambiente

```bash
# Copia il file di esempio nel backend
cp .env.example backend/.env

# Apri backend/.env e imposta la tua chiave Anthropic:
# ANTHROPIC_API_KEY=sk-ant-api03-...

# Copia il file per il frontend
echo "VITE_API_URL=http://localhost:8000" > frontend/.env
echo "VITE_WS_URL=ws://localhost:8000" >> frontend/.env
```

### 4. Script automatico (Windows PowerShell)

Lo script `start-local.ps1` esegue automaticamente tutti i passi rimanenti e avvia i 3 servizi come job in background:

```powershell
# Da eseguire dalla root del progetto, con PostgreSQL e Redis già attivi
.\start-local.ps1
```

Lo script:
1. Verifica che Python, Node.js, PostgreSQL e Redis siano raggiungibili
2. Crea il virtualenv `.venv` se non esiste
3. Installa le dipendenze Python (`python.exe -m pip install -r requirements.txt`)
4. Esegue le migrazioni Alembic (`alembic upgrade head`)
5. Esegue il seed TURBOPRESS-X500 (`python -m app.db.seed`)
6. Installa le dipendenze npm (`npm install`)
7. Avvia backend (porta 8000), Celery worker e frontend (porta 5173) come job PowerShell

### 5. Avvio manuale dei singoli componenti

Se preferisci avviare ogni componente in un terminale separato:

**Terminale 1 — Backend FastAPI:**
```powershell
cd backend

# Carica variabili d'ambiente
$env:DATABASE_URL = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler"
$env:REDIS_URL    = "redis://localhost:6379/0"
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # la tua chiave
$env:ENVIRONMENT  = "development"

# Esegui le migrazioni (solo prima volta)
python -m alembic upgrade head

# Popola i dati mock (solo prima volta)
python -m app.db.seed

# Avvia il server con hot-reload
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir .
```

**Terminale 2 — Celery Worker:**
```powershell
cd backend

# Stesse variabili d'ambiente del terminale 1
$env:DATABASE_URL = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler"
$env:REDIS_URL    = "redis://localhost:6379/0"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:ENVIRONMENT  = "development"

# Avvia il worker Celery
# --pool=solo è NECESSARIO su Windows (Windows non supporta il fork POSIX)
# Su Linux/Mac puoi usare --pool=prefork o --concurrency=4
python -m celery -A celery_worker.celery_app worker --loglevel=info --pool=solo
```

**Terminale 3 — Frontend Vite:**
```powershell
cd frontend
npm install        # solo prima volta
npm run dev
# → http://localhost:5173
```

### Verificare che tutto funzioni

```powershell
# Backend health check
Invoke-RestMethod http://localhost:8000/health
# Risposta: { "status": "ok" }

# Lista endpoint disponibili
Start-Process "http://localhost:8000/docs"

# Frontend
Start-Process "http://localhost:5173"
```

---

## Flusso operativo — Come usare il sistema

1. Apri il **frontend** → http://localhost:5173
2. Vai su **Scenario Manager** → crea un nuovo scenario scegliendo obiettivo (es. `FINISH_BY_DATE`) e data target
3. Clicca **Crea e Schedula** → Celery worker riceve il task e avvia il solver CP-SAT
4. Il badge WebSocket in alto si aggiorna quando lo scheduling è completato (in genere 5–60 secondi)
5. Vai su **Gantt View** → visualizza il piano per operatore o per ordine
6. Vai su **AI Assistant** → chiedi spiegazioni, analisi ritardi, suggerimenti di ottimizzazione
7. Usa **Export** → scarica il piano in CSV (Excel), JSON-SAP o PDF

---

## Variabili d'ambiente

| Variabile | Descrizione | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (asyncpg) | `postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler` |
| `REDIS_URL` | Redis broker URL | `redis://localhost:6379/0` |
| `ANTHROPIC_API_KEY` | Chiave API Anthropic — [ottienila qui](https://console.anthropic.com/) | — |
| `CPSAT_TIMEOUT_SECONDS` | Tempo massimo solver CP-SAT per scenario | `60` |
| `MIN_OP_DURATION_MINUTES` | Durata minima di un'operazione schedulata | `30` |
| `ENVIRONMENT` | `development` o `production` | `development` |
| `VITE_API_URL` | URL del backend visto dal browser | `http://localhost:8000` |
| `VITE_WS_URL` | URL WebSocket visto dal browser | `ws://localhost:8000` |

---

## Struttura del progetto

```
gd-scheduler/
├── backend/
│   ├── app/
│   │   ├── api/routes/        # FastAPI routers (orders, schedule, ai, export, …)
│   │   ├── core/
│   │   │   ├── ai/            # Claude client, prompt builder, context extractor
│   │   │   └── scheduler/     # CP-SAT engine, DAG builder, shift preprocessor
│   │   ├── models/            # SQLAlchemy models (19 tabelle)
│   │   ├── schemas/           # Pydantic v2 schemas
│   │   └── db/                # Session async, seed script TURBOPRESS-X500
│   ├── alembic/               # Migrazioni database
│   ├── celery_worker.py       # Configurazione app Celery
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/             # Dashboard, Gantt, BOM, Calendar, AI, Export, …
│       ├── components/        # Layout, Gantt, BOM, AI sidebar
│       ├── api/               # Axios client + React Query hooks
│       └── store/             # Zustand stores (schedule, operator, ai, ui)
├── docker-compose.yml
├── start-local.ps1            # Script avvio locale Windows
└── .env.example
```

---

## Integrazioni future

### Collegare SAP reale

Il sistema usa attualmente dati mock (seed TURBOPRESS-X500). Per collegare un SAP reale:

1. Sostituisci `backend/app/db/seed.py` con un connettore SAP-RFC o OData.
2. Mappa i campi SAP → modelli `production_orders` e `z_orders_link`.
3. Imposta un job periodico (Celery beat) per sincronizzare gli ordini.
4. Aggiorna `DATABASE_URL` puntando a un PostgreSQL di produzione.
5. Configura SSL/TLS e credenziali sicure tramite secrets manager.

### Moduli aggiuntivi

- **SAP DM Integration**: webhook su eventi di avanzamento operazioni in tempo reale.
- **ERP Feedback**: esporta schedule confermato via `GET /api/export/scenario/{id}/json-sap`.
- **Multi-plant**: estendi `workcenters` con `plant_code` e replica il modello per più stabilimenti.
- **Celery Beat**: aggiungi task periodici (sincronizzazione SAP, pulizia sessioni AI scadute).
