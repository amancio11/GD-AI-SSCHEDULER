<#
.SYNOPSIS
    Avvio locale del MES Production Scheduler senza Docker.

.DESCRIPTION
    Questo script avvia tutti i componenti del sistema localmente:
      1. PostgreSQL 16 portable  — da $env:USERPROFILE\Downloads\pgsql
      2. Redis 5 portable        — da $env:USERPROFILE\Downloads\redis
      3. Backend FastAPI          — porta 8000, hot-reload
      4. Celery Worker            — task asincroni (scheduling, AI proattiva)
      5. Frontend Vite            — porta 5173, hot-reload

    PRE-REQUISITI (setup una-tantum, vedi README.md):
      - PostgreSQL portable estratto in: %USERPROFILE%\Downloads\pgsql
        con cluster inizializzato in:    %USERPROFILE%\Downloads\pgsql-data
        e database "scheduler" creato con utente "scheduler:scheduler"
      - Redis portable estratto in:     %USERPROFILE%\Downloads\redis
      - Python 3.11+ nel PATH
      - Node.js 18+ nel PATH
      - backend\.env compilato (in particolare ANTHROPIC_API_KEY)

.EXAMPLE
    .\start-local.ps1

    # Per fermare tutto:
    Get-Job | Stop-Job ; Get-Job | Remove-Job
    # Per fermare anche PostgreSQL e Redis:
    & "$env:USERPROFILE\Downloads\pgsql\bin\pg_ctl.exe" -D "$env:USERPROFILE\Downloads\pgsql-data" stop
    Stop-Process -Name "redis-server" -ErrorAction SilentlyContinue
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT     = $PSScriptRoot
$BACKEND  = Join-Path $ROOT "backend"
$FRONTEND = Join-Path $ROOT "frontend"
$PG_BIN   = "$env:USERPROFILE\Downloads\pgsql\bin"
$PG_DATA  = "$env:USERPROFILE\Downloads\pgsql-data"
$REDIS_DIR = "$env:USERPROFILE\Downloads\redis"

# ── Colori ───────────────────────────────────────────────────────────────────
function Write-Step([string]$msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "  [ERR] $msg"  -ForegroundColor Red }

# ── 1. Verifica Python e Node ─────────────────────────────────────────────────
Write-Step "Verifica prerequisiti"
try { Write-OK "Python: $(python --version 2>&1)" } catch { Write-Fail "Python non trovato nel PATH"; exit 1 }
try { Write-OK "Node.js: $(node --version 2>&1)"  } catch { Write-Fail "Node.js non trovato nel PATH"; exit 1 }

# ── 2. Avvia PostgreSQL portable ──────────────────────────────────────────────
Write-Step "Avvio PostgreSQL portable"
if (-not (Test-Path "$PG_BIN\pg_ctl.exe")) {
    Write-Fail "PostgreSQL non trovato in $PG_BIN"
    Write-Warn "Segui la sezione 'PostgreSQL portable' del README.md per installarlo"
    exit 1
}
# Controlla se PostgreSQL è già in esecuzione
$pgStatus = & "$PG_BIN\pg_ctl.exe" -D $PG_DATA status 2>&1
if ($pgStatus -match "server is running") {
    Write-OK "PostgreSQL già in esecuzione"
} else {
    & "$PG_BIN\pg_ctl.exe" -D $PG_DATA -l "$PG_DATA\postgres.log" start 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    Write-OK "PostgreSQL avviato (log: $PG_DATA\postgres.log)"
}

# ── 3. Avvia Redis portable ───────────────────────────────────────────────────
Write-Step "Avvio Redis portable"
if (-not (Test-Path "$REDIS_DIR\redis-server.exe")) {
    Write-Fail "Redis non trovato in $REDIS_DIR"
    Write-Warn "Segui la sezione 'Redis portable' del README.md per installarlo"
    exit 1
}
# Controlla se Redis risponde già
$redisPing = & "$REDIS_DIR\redis-cli.exe" ping 2>&1
if ($redisPing -eq "PONG") {
    Write-OK "Redis già in esecuzione"
} else {
    Start-Process -FilePath "$REDIS_DIR\redis-server.exe" `
        -ArgumentList "$REDIS_DIR\redis.windows.conf" -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $redisPing2 = & "$REDIS_DIR\redis-cli.exe" ping 2>&1
    if ($redisPing2 -eq "PONG") { Write-OK "Redis avviato" }
    else { Write-Fail "Redis non risponde dopo l'avvio"; exit 1 }
}

# ── 4. Verifica .env backend ──────────────────────────────────────────────────
Write-Step "Verifica file .env backend"
$envFile = Join-Path $BACKEND ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $ROOT ".env.example") $envFile
    Write-Warn ".env creato da .env.example — imposta ANTHROPIC_API_KEY in backend\.env"
} else {
    Write-OK ".env presente"
    if ((Get-Content $envFile -Raw) -match "INSERISCI_QUI|sk-ant-\.\.\.") {
        Write-Warn "ANTHROPIC_API_KEY non impostata — le funzioni AI non funzioneranno"
    }
}

# Carica .env come dizionario
$envDict = @{}
Get-Content $envFile | Where-Object { $_ -match "^[A-Z]" -and $_ -notmatch "^#" } | ForEach-Object {
    $parts = $_ -split "=", 2
    if ($parts.Length -eq 2) { $envDict[$parts[0].Trim()] = $parts[1].Trim() }
}

# ── 5. Dipendenze Python ──────────────────────────────────────────────────────
Write-Step "Dipendenze Python"
Push-Location $BACKEND
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "  Creazione virtualenv .venv..."
        python -m venv .venv
    }
    # Installa usando python -m pip (evita problemi con il pip.exe del .venv su Windows)
    python -m pip install -q --timeout 120 -r requirements.txt
    Write-OK "Dipendenze Python installate"
} catch {
    Write-Fail "Errore: $_"; Pop-Location; exit 1
} finally { Pop-Location }

# ── 6. Migrazioni Alembic ─────────────────────────────────────────────────────
Write-Step "Migrazioni database (Alembic)"
Push-Location $BACKEND
try {
    foreach ($k in $envDict.Keys) { [System.Environment]::SetEnvironmentVariable($k, $envDict[$k], "Process") }
    python -m alembic upgrade head 2>&1 | Select-Object -Last 3 | Write-Host
    Write-OK "Migrazioni applicate"
} catch {
    Write-Warn "Errore migrazioni (potrebbe essere normale se il DB e' gia' aggiornato): $_"
} finally { Pop-Location }

# ── 7. Seed dati TURBOPRESS-X500 ──────────────────────────────────────────────
Write-Step "Seed dati mock (TURBOPRESS-X500)"
Push-Location $BACKEND
try {
    python -m app.db.seed 2>&1 | Select-Object -Last 5 | Write-Host
    Write-OK "Seed completato (idempotente)"
} catch {
    Write-Warn "Errore seed: $_ (potrebbe essere gia' stato eseguito)"
} finally { Pop-Location }

# ── 8. Dipendenze frontend ────────────────────────────────────────────────────
Write-Step "Dipendenze frontend (npm)"
Push-Location $FRONTEND
try {
    if (-not (Test-Path "node_modules")) { npm install --silent; Write-OK "node_modules installato" }
    else { Write-OK "node_modules gia' presente (skip)" }
} catch {
    Write-Fail "Errore npm install: $_"; Pop-Location; exit 1
} finally { Pop-Location }

# ── 9. Avvio servizi in background ───────────────────────────────────────────
Write-Step "Avvio servizi in background"

$backendJob = Start-Job -Name "mes-backend" -ScriptBlock {
    param($path, $env)
    Set-Location $path
    foreach ($k in $env.Keys) { [System.Environment]::SetEnvironmentVariable($k, $env[$k], "Process") }
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir $path 2>&1
} -ArgumentList $BACKEND, $envDict

$celeryJob = Start-Job -Name "mes-celery" -ScriptBlock {
    param($path, $env)
    Set-Location $path
    foreach ($k in $env.Keys) { [System.Environment]::SetEnvironmentVariable($k, $env[$k], "Process") }
    # --pool=solo obbligatorio su Windows (no fork POSIX)
    python -m celery -A celery_worker.celery_app worker --loglevel=info --pool=solo 2>&1
} -ArgumentList $BACKEND, $envDict

$frontendJob = Start-Job -Name "mes-frontend" -ScriptBlock {
    param($path)
    Set-Location $path
    npm run dev 2>&1
} -ArgumentList $FRONTEND

# ── 10. Riepilogo ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  SISTEMA AVVIATO" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  PostgreSQL  ->  localhost:5432"
Write-Host "  Redis       ->  localhost:6379"
Write-Host "  Backend     ->  http://localhost:8000"
Write-Host "  Swagger UI  ->  http://localhost:8000/docs"
Write-Host "  Frontend    ->  http://localhost:5173"
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Monitorare i log:"
Write-Host "    Receive-Job -Name mes-backend  -Keep"
Write-Host "    Receive-Job -Name mes-celery   -Keep"
Write-Host "    Receive-Job -Name mes-frontend -Keep"
Write-Host ""
Write-Host "  Per fermare i servizi Node/Python/Celery:"
Write-Host "    Get-Job | Stop-Job ; Get-Job | Remove-Job"
Write-Host ""
Write-Host "  Per fermare anche PostgreSQL e Redis:"
Write-Host "    & `"$PG_BIN\pg_ctl.exe`" -D `"$PG_DATA`" stop"
Write-Host "    Stop-Process -Name redis-server -ErrorAction SilentlyContinue"
Write-Host ""
Write-Host "  [Premere CTRL+C per fermare backend/Celery/frontend]" -ForegroundColor Yellow
Write-Host ""

try {
    while ($true) {
        Start-Sleep -Seconds 5
        $out = Receive-Job -Name "mes-backend" -Keep 2>&1 | Select-Object -Last 2
        if ($out) { $out | ForEach-Object { Write-Host "  [backend] $_" -ForegroundColor DarkGray } }
    }
} finally {
    Write-Host "`nArresto job backend/Celery/frontend..." -ForegroundColor Yellow
    Get-Job | Stop-Job
    Get-Job | Remove-Job
    Write-Host "Job fermati. PostgreSQL e Redis continuano a girare in background." -ForegroundColor Green
}


.DESCRIPTION
    Questo script avvia tutti i componenti del sistema localmente:
      1. PostgreSQL  — deve essere già installato e in esecuzione (porta 5432)
      2. Redis       — deve essere già installato e in esecuzione (porta 6379)
      3. Backend     — FastAPI + uvicorn (porta 8000, hot-reload)
      4. Celery      — worker per task asincroni (scheduling, AI proattiva)
      5. Frontend    — Vite dev server (porta 5173, hot-reload)

    PRE-REQUISITI:
      - Python 3.11+ installato (python.exe nel PATH)
      - Node.js 18+ installato (node.exe nel PATH)
      - PostgreSQL 16 installato e avviato (servizio "postgresql-x64-16" o simile)
      - Redis 7 installato e avviato (o usare: winget install Redis.Redis)
      - Database "scheduler" creato con utente "scheduler:scheduler"
        (vedi README.md sezione "Setup Database Locale")
      - backend/.env compilato con la tua ANTHROPIC_API_KEY

.EXAMPLE
    .\start-local.ps1

    # Per fermare tutto: chiudi le finestre PowerShell aperte dallo script
    # oppure usa: Get-Job | Stop-Job ; Get-Job | Remove-Job
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT    = $PSScriptRoot
$BACKEND = Join-Path $ROOT "backend"
$FRONTEND = Join-Path $ROOT "frontend"

# ── Colori per output leggibile ───────────────────────────────────────────────
function Write-Step([string]$msg) {
    Write-Host "`n[STEP] $msg" -ForegroundColor Cyan
}
function Write-OK([string]$msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}
function Write-Warn([string]$msg) {
    Write-Host "  [!!] $msg" -ForegroundColor Yellow
}
function Write-Fail([string]$msg) {
    Write-Host "  [ERR] $msg" -ForegroundColor Red
}

# ── 1. Verifica prerequisiti ──────────────────────────────────────────────────
Write-Step "Verifica prerequisiti"

# Python
try {
    $pyVer = python --version 2>&1
    Write-OK "Python: $pyVer"
} catch {
    Write-Fail "Python non trovato nel PATH. Installa Python 3.11+ da https://python.org"
    exit 1
}

# Node
try {
    $nodeVer = node --version 2>&1
    Write-OK "Node.js: $nodeVer"
} catch {
    Write-Fail "Node.js non trovato nel PATH. Installa Node.js 18+ da https://nodejs.org"
    exit 1
}

# PostgreSQL raggiungibile
Write-Step "Verifica connettività PostgreSQL (localhost:5432)"
$pgReachable = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("localhost", 5432)
    $tcp.Close()
    $pgReachable = $true
    Write-OK "PostgreSQL raggiungibile su localhost:5432"
} catch {
    Write-Fail "PostgreSQL NON raggiungibile su localhost:5432."
    Write-Warn "Avvia PostgreSQL prima di eseguire questo script."
    Write-Warn "Su Windows: Start-Service 'postgresql-x64-16'  (nome servizio potrebbe variare)"
    Write-Warn "In alternativa: docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=scheduler -e POSTGRES_USER=scheduler -e POSTGRES_DB=scheduler postgres:16"
    exit 1
}

# Redis raggiungibile
Write-Step "Verifica connettività Redis (localhost:6379)"
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("localhost", 6379)
    $tcp.Close()
    Write-OK "Redis raggiungibile su localhost:6379"
} catch {
    Write-Fail "Redis NON raggiungibile su localhost:6379."
    Write-Warn "Installa Redis con: winget install Redis.Redis"
    Write-Warn "Oppure avvia solo Redis via Docker:"
    Write-Warn "  docker run -d -p 6379:6379 redis:7"
    exit 1
}

# ── 2. Verifica .env backend ──────────────────────────────────────────────────
Write-Step "Verifica file .env backend"
$envFile = Join-Path $BACKEND ".env"
if (-not (Test-Path $envFile)) {
    Write-Warn ".env non trovato — copio da .env.example"
    Copy-Item (Join-Path $ROOT ".env.example") $envFile
    Write-Warn "ATTENZIONE: aggiorna ANTHROPIC_API_KEY in backend/.env prima di usare le funzioni AI"
} else {
    Write-OK ".env presente"
    # Avvisa se la chiave Anthropic non è stata impostata
    $envContent = Get-Content $envFile -Raw
    if ($envContent -match "INSERISCI_QUI|sk-ant-\.\.\." ) {
        Write-Warn "ANTHROPIC_API_KEY non configurata — le funzioni AI non funzioneranno"
    }
}

# ── 3. Dipendenze Python ──────────────────────────────────────────────────────
Write-Step "Installazione dipendenze Python"
Push-Location $BACKEND
try {
    # Crea virtualenv se non esiste
    if (-not (Test-Path ".venv")) {
        Write-Host "  Creazione virtualenv .venv..."
        python -m venv .venv
    }

    # Attiva virtualenv e installa dipendenze
    $activateScript = Join-Path $BACKEND ".venv\Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        & $activateScript
        Write-OK "Virtualenv attivato"
        pip install --quiet --timeout 120 -r requirements.txt
    } else {
        # Fallback: usa python di sistema
        python -m pip install --quiet --timeout 120 -r requirements.txt
    }
    Write-OK "Dipendenze Python installate"
} catch {
    Write-Fail "Errore installazione dipendenze: $_"
    Pop-Location
    exit 1
} finally {
    Pop-Location
}

# ── 4. Migrazioni database (Alembic) ─────────────────────────────────────────
Write-Step "Esecuzione migrazioni Alembic"
Push-Location $BACKEND
try {
    # Carica variabili d'ambiente dal .env
    $envVars = Get-Content $envFile | Where-Object { $_ -match "^[A-Z]" -and $_ -notmatch "^#" }
    foreach ($line in $envVars) {
        $parts = $line -split "=", 2
        if ($parts.Length -eq 2) {
            [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }

    python -m alembic upgrade head 2>&1 | Write-Host
    Write-OK "Migrazioni applicate"
} catch {
    Write-Warn "Errore migrazioni (potrebbe essere normale se il DB è già aggiornato): $_"
} finally {
    Pop-Location
}

# ── 5. Seed dati TURBOPRESS-X500 ──────────────────────────────────────────────
Write-Step "Seed dati mock (TURBOPRESS-X500)"
Push-Location $BACKEND
try {
    python -m app.db.seed 2>&1 | Select-Object -Last 5 | Write-Host
    Write-OK "Seed completato (idempotente — sicuro rieseguirlo)"
} catch {
    Write-Warn "Errore seed: $_ (potrebbe essere già stato eseguito)"
} finally {
    Pop-Location
}

# ── 6. Dipendenze frontend ────────────────────────────────────────────────────
Write-Step "Installazione dipendenze frontend (npm)"
Push-Location $FRONTEND
try {
    if (-not (Test-Path "node_modules")) {
        npm install --silent
        Write-OK "node_modules installato"
    } else {
        Write-OK "node_modules già presente (skip)"
    }
} catch {
    Write-Fail "Errore npm install: $_"
    Pop-Location
    exit 1
} finally {
    Pop-Location
}

# ── 7. Avvio processi in background ──────────────────────────────────────────
Write-Step "Avvio servizi in background"

# Carica .env per i job background
$envContent = Get-Content $envFile | Where-Object { $_ -match "^[A-Z]" -and $_ -notmatch "^#" }
$envDict = @{}
foreach ($line in $envContent) {
    $parts = $line -split "=", 2
    if ($parts.Length -eq 2) {
        $envDict[$parts[0].Trim()] = $parts[1].Trim()
    }
}

# Backend FastAPI
$backendJob = Start-Job -Name "backend" -ScriptBlock {
    param($backendPath, $envDict)
    Set-Location $backendPath
    foreach ($key in $envDict.Keys) {
        [System.Environment]::SetEnvironmentVariable($key, $envDict[$key], "Process")
    }
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload `
        --app-dir $backendPath 2>&1
} -ArgumentList $BACKEND, $envDict

Write-OK "Backend avviato (Job ID: $($backendJob.Id)) → http://localhost:8000"
Write-OK "  Docs API: http://localhost:8000/docs"

# Celery worker (task asincroni: scheduling CP-SAT, AI proattiva)
$celeryJob = Start-Job -Name "celery" -ScriptBlock {
    param($backendPath, $envDict)
    Set-Location $backendPath
    foreach ($key in $envDict.Keys) {
        [System.Environment]::SetEnvironmentVariable($key, $envDict[$key], "Process")
    }
    python -m celery -A celery_worker.celery_app worker --loglevel=info `
        --pool=solo 2>&1  # --pool=solo necessario su Windows (no fork)
} -ArgumentList $BACKEND, $envDict

Write-OK "Celery worker avviato (Job ID: $($celeryJob.Id))"

# Frontend Vite
$frontendJob = Start-Job -Name "frontend" -ScriptBlock {
    param($frontendPath)
    Set-Location $frontendPath
    npm run dev 2>&1
} -ArgumentList $FRONTEND

Write-OK "Frontend avviato (Job ID: $($frontendJob.Id)) → http://localhost:5173"

# ── 8. Riepilogo ─────────────────────────────────────────────────────────────
Write-Host "`n" + ("─" * 60) -ForegroundColor DarkGray
Write-Host "  SISTEMA AVVIATO" -ForegroundColor Green
Write-Host ("─" * 60) -ForegroundColor DarkGray
Write-Host "  Backend API  →  http://localhost:8000"
Write-Host "  Swagger UI   →  http://localhost:8000/docs"
Write-Host "  ReDoc        →  http://localhost:8000/redoc"
Write-Host "  Frontend     →  http://localhost:5173"
Write-Host ("─" * 60) -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Per monitorare i log dei job:"
Write-Host "    Receive-Job -Name backend -Keep"
Write-Host "    Receive-Job -Name celery  -Keep"
Write-Host "    Receive-Job -Name frontend -Keep"
Write-Host ""
Write-Host "  Per fermare tutto:"
Write-Host "    Get-Job | Stop-Job ; Get-Job | Remove-Job"
Write-Host ""

# Attendi in loop e mostra log backend (ultimi 5 secondi)
Write-Host "  [Premere CTRL+C per fermare tutti i servizi]`n" -ForegroundColor Yellow
try {
    while ($true) {
        Start-Sleep -Seconds 5
        $backendOut = Receive-Job -Name "backend" -Keep 2>&1 | Select-Object -Last 3
        if ($backendOut) {
            $backendOut | ForEach-Object { Write-Host "  [backend] $_" -ForegroundColor DarkGray }
        }
    }
} finally {
    Write-Host "`nArresto servizi..." -ForegroundColor Yellow
    Get-Job | Stop-Job
    Get-Job | Remove-Job
    Write-Host "Tutti i servizi fermati." -ForegroundColor Green
}
