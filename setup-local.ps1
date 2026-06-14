# Setup locale MES Production Scheduler — Windows
# Esegui come Amministratore: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
# Poi: .\setup-local.ps1

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== MES Production Scheduler — Setup Locale ===" -ForegroundColor Cyan

# ─── 1. Verifica prerequisiti ──────────────────────────────────────────────────
Write-Host "`n[1/6] Verifica prerequisiti..." -ForegroundColor Yellow

# Python 3.11+ richiesto (3.12 ideale)
$pyVersion = (python --version 2>&1) -replace "Python ", ""
if ([version]$pyVersion -lt [version]"3.11") {
    Write-Host "  Installo Python 3.12 via winget..." -ForegroundColor Gray
    winget install Python.Python.3.12 --silent --accept-package-agreements
}
Write-Host "  Python: OK ($pyVersion)" -ForegroundColor Green

# Node.js 18+
$nodeVersion = (node --version 2>&1) -replace "v", ""
Write-Host "  Node.js: OK ($nodeVersion)" -ForegroundColor Green

# ─── 2. Installa PostgreSQL 16 via winget ─────────────────────────────────────
Write-Host "`n[2/6] PostgreSQL..." -ForegroundColor Yellow
$pgInstalled = Get-Command psql -ErrorAction SilentlyContinue
if (-not $pgInstalled) {
    Write-Host "  Installo PostgreSQL 16..." -ForegroundColor Gray
    winget install PostgreSQL.PostgreSQL.16 --silent --accept-package-agreements
    # Aggiunge psql al PATH
    $env:PATH += ";C:\Program Files\PostgreSQL\16\bin"
    [System.Environment]::SetEnvironmentVariable("PATH", $env:PATH, "Machine")
    Write-Host "  NOTA: Riavvia il terminale dopo l'installazione per aggiornare il PATH" -ForegroundColor Yellow
}
Write-Host "  PostgreSQL: OK" -ForegroundColor Green

# Crea database e utente scheduler
Write-Host "  Configurazione database scheduler..." -ForegroundColor Gray
$pgSetup = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scheduler') THEN
    CREATE USER scheduler WITH PASSWORD 'scheduler';
  END IF;
END
`$`$;

SELECT 'CREATE DATABASE scheduler OWNER scheduler'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'scheduler')\gexec
"@
$pgSetup | psql -U postgres -c $pgSetup 2>$null
Write-Host "  Database 'scheduler' configurato" -ForegroundColor Green

# ─── 3. Redis via WSL ─────────────────────────────────────────────────────────
Write-Host "`n[3/6] Redis (via WSL)..." -ForegroundColor Yellow

# Controlla se c'è una distro WSL installata
$wslDistros = wsl --list --quiet 2>&1
if ($wslDistros -match "Ubuntu|Debian") {
    Write-Host "  WSL disponibile — avvio Redis in background..." -ForegroundColor Gray
    # Avvia Redis via WSL in background (rimane attivo finché non si chiude WSL)
    Start-Process -WindowStyle Hidden wsl -ArgumentList "sudo apt-get install -y redis-server 2>/dev/null; redis-server --daemonize yes"
    Start-Sleep -Seconds 3
    Write-Host "  Redis avviato su localhost:6379" -ForegroundColor Green
} else {
    Write-Host "  WSL non configurato. Opzioni:" -ForegroundColor Yellow
    Write-Host "    A) Installa WSL: wsl --install -d Ubuntu" -ForegroundColor Gray
    Write-Host "    B) Scarica Memurai (Redis per Windows): https://www.memurai.com/get-memurai" -ForegroundColor Gray
    Write-Host "    C) Usa Docker Desktop con solo Redis: docker run -d -p 6379:6379 redis:7-alpine" -ForegroundColor Gray
    Write-Host "  Lo scheduler funziona senza Redis ma le task Celery (rischedulazione) non saranno disponibili." -ForegroundColor Yellow
}

# ─── 4. Backend Python — Virtual Environment ──────────────────────────────────
Write-Host "`n[4/6] Backend Python..." -ForegroundColor Yellow

Set-Location "$ROOT\backend"

# Crea venv se non esiste
if (-not (Test-Path ".venv")) {
    Write-Host "  Creazione virtual environment..." -ForegroundColor Gray
    python -m venv .venv
}

# Attiva venv
& ".venv\Scripts\Activate.ps1"

# Installa dipendenze
Write-Host "  Installazione dipendenze Python (può richiedere 2-3 minuti)..." -ForegroundColor Gray
pip install -r requirements.txt --quiet

# Configurazione .env locale
if (-not (Test-Path "$ROOT\.env")) {
    Copy-Item "$ROOT\.env.example" "$ROOT\.env"
    Write-Host "  Creato .env — IMPOSTA ANTHROPIC_API_KEY prima di avviare!" -ForegroundColor Yellow
}

# Alembic migration
Write-Host "  Esecuzione migrazioni database..." -ForegroundColor Gray
$env:DATABASE_URL = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler"
alembic upgrade head

# Seed dati mock TURBOPRESS-X500
Write-Host "  Caricamento dati mock TURBOPRESS-X500..." -ForegroundColor Gray
python -m app.db.seed

Write-Host "  Backend configurato" -ForegroundColor Green

# ─── 5. Frontend Node ─────────────────────────────────────────────────────────
Write-Host "`n[5/6] Frontend..." -ForegroundColor Yellow

Set-Location "$ROOT\frontend"
Write-Host "  Installazione dipendenze npm..." -ForegroundColor Gray
npm install --silent
Write-Host "  Frontend configurato" -ForegroundColor Green

# ─── 6. Riepilogo ─────────────────────────────────────────────────────────────
Write-Host "`n[6/6] Setup completato!" -ForegroundColor Green
Write-Host ""
Write-Host "Per avviare il progetto, apri DUE terminali:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  TERMINALE 1 — Backend:" -ForegroundColor White
Write-Host "    cd backend" -ForegroundColor Gray
Write-Host "    .venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "    uvicorn app.main:app --reload --port 8000" -ForegroundColor Gray
Write-Host ""
Write-Host "  TERMINALE 2 — Frontend:" -ForegroundColor White
Write-Host "    cd frontend" -ForegroundColor Gray
Write-Host "    npm run dev" -ForegroundColor Gray
Write-Host ""
Write-Host "  Opzionale — Celery Worker (rischedulazione in background):" -ForegroundColor White
Write-Host "    cd backend" -ForegroundColor Gray
Write-Host "    .venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "    celery -A celery_worker worker --loglevel=info" -ForegroundColor Gray
Write-Host ""
Write-Host "  Frontend: http://localhost:5173" -ForegroundColor Cyan
Write-Host "  Backend API: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  Health check: http://localhost:8000/health" -ForegroundColor Cyan
