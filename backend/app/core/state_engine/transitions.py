"""State Engine — transizioni di stato per Operation, ScheduleEntry, ProductionOrder.

DESIGN
======
Schema PERMISSIVO: qualunque transizione è tecnicamente ammessa (il planner ha
sempre l'ultima parola — può sempre riaprire un'operazione COMPLETED, annullare
un BLOCKED, ecc.). Il modulo non solleva mai un'eccezione per "transizione non
valida": al massimo marca la transizione come `is_unusual=True` e produce un
messaggio di warning destinato all'audit log.

Questo file è puro (nessun I/O, nessuna dipendenza da SQLAlchemy/FastAPI) per
essere testabile in isolamento e riusabile sia dal router REST sia dal Celery
task di reschedule.

Ogni chiamata a `transition_operation_status(...)` ritorna un `TransitionResult`
che descrive:
  - lo stato risultante (operation_status, entry_status)
  - gli effetti collaterali da applicare (delay_minutes, serve reschedule?)
  - un messaggio di audit in italiano, pronto per il log/DB

Il chiamante (router/engine) è responsabile di persistere `TransitionResult`
sui modelli SQLAlchemy e di loggare l'evento in `audit_log` (vedi audit.py).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from app.enums import OperationStatus, ScheduleEntryStatus, ProductionOrderStatus


# ──────────────────────────────────────────────────────────────────────────
# Mappa "canonica" Operation → ScheduleEntry
# ──────────────────────────────────────────────────────────────────────────
# Usata come default quando il chiamante non specifica esplicitamente lo stato
# della entry: tiene i due stati allineati nel caso comune.
_OP_TO_ENTRY_STATUS: dict[OperationStatus, ScheduleEntryStatus] = {
    OperationStatus.PENDING: ScheduleEntryStatus.SCHEDULED,
    OperationStatus.IN_PROGRESS: ScheduleEntryStatus.IN_PROGRESS,
    OperationStatus.COMPLETED: ScheduleEntryStatus.COMPLETED,
    OperationStatus.BLOCKED: ScheduleEntryStatus.SCHEDULED,
    OperationStatus.INTERRUPTED: ScheduleEntryStatus.INTERRUPTED,
}

# Transizioni "attese" in un workflow MES standard. Usata SOLO per decidere se
# loggare la transizione come `is_unusual` — non blocca mai nulla.
_EXPECTED_OPERATION_TRANSITIONS: dict[OperationStatus, set[OperationStatus]] = {
    OperationStatus.PENDING: {OperationStatus.IN_PROGRESS, OperationStatus.BLOCKED},
    OperationStatus.IN_PROGRESS: {
        OperationStatus.COMPLETED,
        OperationStatus.INTERRUPTED,
        OperationStatus.BLOCKED,
    },
    OperationStatus.INTERRUPTED: {OperationStatus.IN_PROGRESS, OperationStatus.BLOCKED},
    OperationStatus.BLOCKED: {OperationStatus.PENDING, OperationStatus.IN_PROGRESS},
    OperationStatus.COMPLETED: set(),  # qualunque uscita da COMPLETED è "unusual" (riapertura)
}


class RescheduleUrgency(str, enum.Enum):
    """Quanto è urgente rilanciare il solver CP-SAT dopo questa transizione."""

    NONE = "NONE"  # nessun impatto sullo schedule (es. PENDING→BLOCKED senza data)
    SOFT = "SOFT"  # ritardo sotto soglia: aggiorna solo i dati, nessun reschedule
    HARD = "HARD"  # ritardo sopra soglia o evento strutturale: reschedule CP-SAT completo


@dataclass(slots=True)
class TransitionResult:
    """Esito della validazione/applicazione di una transizione di stato."""

    previous_status: OperationStatus
    operation_status: OperationStatus
    entry_status: ScheduleEntryStatus
    is_unusual: bool
    audit_message: str
    delay_minutes: int = 0
    reschedule_urgency: RescheduleUrgency = RescheduleUrgency.NONE
    warnings: list[str] = field(default_factory=list)


def _compute_delay_minutes(
    scheduled_end: datetime | None,
    actual_end: datetime | None,
) -> int:
    """Ritorna il ritardo in minuti (>=0). 0 se in anticipo o senza dati."""
    if scheduled_end is None or actual_end is None:
        return 0
    delta = actual_end - scheduled_end
    minutes = int(delta.total_seconds() // 60)
    return max(0, minutes)


def transition_operation_status(
    *,
    current_status: OperationStatus,
    new_status: OperationStatus,
    scheduled_end: datetime | None,
    actual_end: datetime | None,
    delay_threshold_minutes: int,
    interruption_reason: str | None = None,
    entry_status_override: ScheduleEntryStatus | None = None,
) -> TransitionResult:
    """Calcola l'esito di una transizione di stato per un'Operation.

    Parametri
    ---------
    current_status, new_status:
        Stato attuale e richiesto dell'operazione.
    scheduled_end, actual_end:
        Usati per calcolare il ritardo quando new_status == COMPLETED.
    delay_threshold_minutes:
        Soglia (minuti) sopra la quale il ritardo è considerato "HARD" e deve
        scatenare un reschedule CP-SAT completo. Sotto soglia: "SOFT" (si
        aggiornano solo i dati, nessun reschedule automatico).
    interruption_reason:
        Obbligatorio "moralmente" se new_status == INTERRUPTED, ma non bloccante
        (schema permissivo): se assente viene solo aggiunto un warning.
    entry_status_override:
        Se il chiamante vuole forzare uno stato diverso da quello canonico
        per la ScheduleEntry (es. DELAYED invece di COMPLETED).
    """
    warnings: list[str] = []

    # ── 1. Determina se la transizione è "inusuale" (solo per audit/log) ─────
    expected_targets = _EXPECTED_OPERATION_TRANSITIONS.get(current_status, set())
    is_unusual = new_status not in expected_targets and new_status != current_status

    if is_unusual:
        warnings.append(
            f"Transizione {current_status.value} → {new_status.value} non rientra "
            "nel workflow standard (operazione probabilmente riaperta manualmente)."
        )

    if new_status == OperationStatus.INTERRUPTED and not interruption_reason:
        warnings.append(
            "Operazione interrotta senza motivo specificato (interruption_reason mancante)."
        )

    # ── 2. Calcola ritardo e urgenza di reschedule ────────────────────────────
    delay_minutes = 0
    urgency = RescheduleUrgency.NONE

    if new_status == OperationStatus.COMPLETED:
        delay_minutes = _compute_delay_minutes(scheduled_end, actual_end)
        if delay_minutes > 0:
            urgency = (
                RescheduleUrgency.HARD
                if delay_minutes >= delay_threshold_minutes
                else RescheduleUrgency.SOFT
            )
    elif new_status in (OperationStatus.BLOCKED, OperationStatus.INTERRUPTED):
        # Un blocco/interruzione non ha "ritardo" misurabile finché non riprende,
        # ma può comunque richiedere un reschedule per liberare la capacità
        # dell'operatore e ripianificare i successori bloccati.
        if current_status == OperationStatus.IN_PROGRESS:
            urgency = RescheduleUrgency.HARD

    # ── 3. Stato ScheduleEntry coerente ──────────────────────────────────────
    entry_status = entry_status_override or _OP_TO_ENTRY_STATUS[new_status]
    if new_status == OperationStatus.COMPLETED and delay_minutes > 0:
        # L'entry riflette il ritardo anche se l'operazione è COMPLETED
        entry_status = ScheduleEntryStatus.DELAYED

    # ── 4. Messaggio di audit in italiano ─────────────────────────────────────
    audit_message = _build_audit_message(
        current_status, new_status, delay_minutes, urgency, is_unusual
    )

    return TransitionResult(
        previous_status=current_status,
        operation_status=new_status,
        entry_status=entry_status,
        is_unusual=is_unusual,
        audit_message=audit_message,
        delay_minutes=delay_minutes,
        reschedule_urgency=urgency,
        warnings=warnings,
    )


def _build_audit_message(
    current_status: OperationStatus,
    new_status: OperationStatus,
    delay_minutes: int,
    urgency: RescheduleUrgency,
    is_unusual: bool,
) -> str:
    base = f"Stato operazione: {current_status.value} → {new_status.value}"
    if delay_minutes > 0:
        base += f" (ritardo {delay_minutes} min, urgenza reschedule={urgency.value})"
    if is_unusual:
        base += " [INUSUALE]"
    return base


# ──────────────────────────────────────────────────────────────────────────
# Rollup stato ProductionOrder — bottom-up dalla BOM
# ──────────────────────────────────────────────────────────────────────────

# Priorità di "dominanza" tra stati figli: se anche un solo figlio ha uno
# stato con priorità più alta, l'ordine padre eredita quello stato.
# (BLOCKED domina su tutto: un blocco a valle blocca il padre logicamente;
#  IN_PROGRESS domina su PLANNED/COMPLETED misti; COMPLETED solo se TUTTI lo sono)
_STATUS_PRIORITY: dict[ProductionOrderStatus, int] = {
    ProductionOrderStatus.BLOCKED: 4,
    ProductionOrderStatus.MISSING: 3,
    ProductionOrderStatus.IN_PROGRESS: 2,
    ProductionOrderStatus.PLANNED: 1,
    ProductionOrderStatus.COMPLETED: 0,  # COMPLETED vince solo se è unanime
}


def compute_rollup_status(
    child_statuses: list[ProductionOrderStatus],
    current_status: ProductionOrderStatus,
) -> ProductionOrderStatus:
    """Deriva lo stato di un ProductionOrder dai suoi figli diretti nella BOM.

    Regole (in ordine di applicazione):
      1. MISSING è sticky: se lo stato attuale è MISSING, il rollup NON lo
         sovrascrive automaticamente — resta manuale finché un componente
         mancante non viene marcato come arrivato (vedi missing_components).
      2. Nessun figlio (es. GROUP con soli componenti, o ordine foglia) →
         lo stato non viene derivato: ritorna lo stato attuale invariato.
      3. Se TUTTI i figli sono COMPLETED → COMPLETED.
      4. Se ALMENO UN figlio è BLOCKED → BLOCKED (un blocco a valle impatta
         il padre, perché un Reference Point dipendente non potrà chiudersi).
      5. Se ALMENO UN figlio è MISSING → MISSING (propaga la criticità).
      6. Se ALMENO UN figlio è IN_PROGRESS o COMPLETED (ma non tutti) →
         IN_PROGRESS.
      7. Altrimenti (tutti PLANNED) → PLANNED.
    """
    if current_status == ProductionOrderStatus.MISSING:
        return ProductionOrderStatus.MISSING

    if not child_statuses:
        return current_status

    if all(s == ProductionOrderStatus.COMPLETED for s in child_statuses):
        return ProductionOrderStatus.COMPLETED

    if any(s == ProductionOrderStatus.BLOCKED for s in child_statuses):
        return ProductionOrderStatus.BLOCKED

    if any(s == ProductionOrderStatus.MISSING for s in child_statuses):
        return ProductionOrderStatus.MISSING

    if any(
        s in (ProductionOrderStatus.IN_PROGRESS, ProductionOrderStatus.COMPLETED)
        for s in child_statuses
    ):
        return ProductionOrderStatus.IN_PROGRESS

    return ProductionOrderStatus.PLANNED