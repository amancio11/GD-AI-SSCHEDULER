<#
.SYNOPSIS
    Avvio del MES Production Scheduler con Podman (alternativa a start-local.ps1).

.DESCRIPTION
    Avvia l'intero stack in container Podman:
      postgres, redis, migrate (Alembic), backend (FastAPI), worker (Celery), frontend (Vite).

    Vantaggio vs start-local.ps1: nessun binario portable da gestire (Postgres/Redis
    girano in container), un solo comando, nessun terminale multiplo.

    PRE-REQUISITI:
      - Podman Desktop / Podman CLI installato e nel PATH
      - Una macchina Podman avviata (su Windows: `podman machine init` una-tantum)
      - File .env nella root (se manca, viene creato da .env.example)
      - Il progetto deve trovarsi sotto la home utente, così che la podman machine
        possa fare il bind-mount del codice (hot-reload).

.PARAMETER Seed
    Esegue anche il seed dei dati mock (TURBOPRESS-X500) dopo l'avvio.

.PARAMETER Down
    Ferma e rimuove i container (i dati del DB nel volume restano).

.EXAMPLE
    .\start-podman.ps1            # avvia tutto
    .\start-podman.ps1 -Seed      # avvia + popola i dati mock
    .\start-podman.ps1 -Down      # ferma tutto
#>

param(
    [switch]$Seed,
    [switch]$Down
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT    = $PSScriptRoot
$COMPOSE = Join-Path $ROOT "podman-compose.yml"

function Write-Step([string]$m) { Write-Host "`n[STEP] $m" -ForegroundColor Cyan }
function Write-OK([string]$m)   { Write-Host "  [OK] $m"   -ForegroundColor Green }
function Write-Warn([string]$m) { Write-Host "  [!!] $m"   -ForegroundColor Yellow }
function Write-Fail([string]$m) { Write-Host "  [ERR] $m"  -ForegroundColor Red }

# ── 1. Verifica Podman ────────────────────────────────────────────────────────
Write-Step "Verifica Podman"
try { Write-OK "Podman: $(podman --version 2>&1)" }
catch { Write-Fail "Podman non trovato nel PATH. Installa Podman Desktop."; exit 1 }

# Rileva il comando compose: `podman compose` (preferito) oppure `podman-compose`.
$composeCmd = $null
podman compose version *> $null
if ($LASTEXITCODE -eq 0) {
    $composeCmd = @("podman", "compose")
    Write-OK "Uso 'podman compose'"
} elseif (Get-Command podman-compose -ErrorAction SilentlyContinue) {
    $composeCmd = @("podman-compose")
    Write-OK "Uso 'podman-compose'"
} else {
    Write-Fail "Nessun provider compose trovato. Installa 'podman compose' o 'podman-compose'."
    exit 1
}

function Invoke-Compose([string[]]$composeArgs) {
    $exe  = $composeCmd[0]
    $base = @()
    if ($composeCmd.Count -gt 1) { $base += $composeCmd[1..($composeCmd.Count-1)] }
    & $exe @base -f $COMPOSE @composeArgs
}

# ── 2. Macchina Podman attiva (Windows/macOS) ─────────────────────────────────
Write-Step "Verifica macchina Podman"
$machines = (podman machine list --format "{{.Name}} {{.Running}}" 2>$null)
if ($machines) {
    if ($machines -notmatch "true|Currently running") {
        Write-Warn "Macchina Podman non in esecuzione — avvio..."
        podman machine start
    }
    Write-OK "Macchina Podman attiva"
} else {
    Write-Warn "Nessuna macchina Podman trovata. Su Windows esegui una-tantum: podman machine init"
}

# ── 3. Down ───────────────────────────────────────────────────────────────────
if ($Down) {
    Write-Step "Arresto stack"
    Invoke-Compose @("down")
    Write-OK "Stack fermato (volume DB conservato; usa 'down -v' per cancellarlo)"
    exit 0
}

# ── 4. File .env ──────────────────────────────────────────────────────────────
Write-Step "Verifica .env"
$envFile = Join-Path $ROOT ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $ROOT ".env.example") $envFile
    Write-Warn ".env creato da .env.example — inserisci ANTHROPIC_API_KEY per le funzioni AI"
} else {
    Write-OK ".env presente"
}

# ── 5. Build & up ─────────────────────────────────────────────────────────────
Write-Step "Build e avvio container"
Invoke-Compose @("up", "-d", "--build")
Write-OK "Container avviati"

# ── 6. Seed opzionale ─────────────────────────────────────────────────────────
if ($Seed) {
    Write-Step "Seed dati mock (TURBOPRESS-X500)"
    Invoke-Compose @("--profile", "seed", "run", "--rm", "seed")
    Write-OK "Seed completato"
}

Write-Host ""
Write-OK "Frontend:  http://localhost:5173"
Write-OK "Backend:   http://localhost:8000  (docs: /docs)"
Write-Host ""
Write-Host "Log:    " -NoNewline; Write-Host "podman compose -f podman-compose.yml logs -f backend worker" -ForegroundColor DarkGray
Write-Host "Stop:   " -NoNewline; Write-Host ".\start-podman.ps1 -Down" -ForegroundColor DarkGray
if (-not $Seed) {
    Write-Host "Seed:   " -NoNewline; Write-Host ".\start-podman.ps1 -Seed   (oppure -Seed al primo avvio)" -ForegroundColor DarkGray
}
