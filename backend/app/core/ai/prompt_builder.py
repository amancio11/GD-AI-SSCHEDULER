"""Prompt builders for all AI modalities.

Each function receives structured context dicts and returns a ready-to-send
prompt string.  Context is JSON-serialised and truncated to stay under
~4 000 tokens (~16 000 chars).
"""
from __future__ import annotations

import json

_MAX_CONTEXT_CHARS = 12_000   # conservative limit


def _ctx_str(data: dict) -> str:
    """Serialise *data* to a JSON string, truncated if too long."""
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    if len(raw) > _MAX_CONTEXT_CHARS:
        raw = raw[:_MAX_CONTEXT_CHARS] + "\n... (troncato per limite di contesto)"
    return raw


SYSTEM_PROMPT_BASE = """
Sei un esperto di production scheduling per macchine industriali complesse.
Hai accesso al contesto completo dello schedule corrente.
Rispondi sempre in italiano. Sii preciso e conciso.
Quando suggerisci azioni, sii specifico: nomina le operazioni, gli operatori e le date.
Rispondi sempre con un oggetto JSON valido a meno che non ti venga chiesta una risposta in testo libero.
""".strip()


def build_optimize_prompt(context: dict) -> str:
    """Prompt per ottimizzazione schedule — chiede suggerimenti concreti."""
    return f"""Analizza il seguente contesto di schedule e suggerisci le 3-5 azioni concrete
più efficaci per ottimizzare makespan, utilizzo delle risorse e rispetto delle scadenze.

CONTESTO:
{_ctx_str(context)}

Rispondi con JSON strutturato:
{{
  "summary": "breve analisi della situazione attuale",
  "suggestions": [
    {{
      "priority": 1,
      "action": "descrizione azione concreta",
      "impact": "impatto stimato",
      "apply_command": "comando/operazione da eseguire (opzionale)"
    }}
  ],
  "estimated_improvement": "stima miglioramento makespan in ore/giorni"
}}"""


def build_delay_analysis_prompt(delay_context: dict, schedule_context: dict) -> str:
    """Prompt per analisi impatto di un ritardo specifico."""
    return f"""Analizza l'impatto del ritardo descritto e suggerisci azioni di mitigazione.

RITARDO:
{_ctx_str(delay_context)}

SCHEDULE CORRENTE:
{_ctx_str(schedule_context)}

Rispondi con JSON strutturato:
{{
  "summary": "sintesi dell'impatto",
  "impacted_operations": ["lista operazioni impattate"],
  "estimated_delta_days": 0.0,
  "mitigation_actions": ["azioni concrete per ridurre l'impatto"],
  "critical_path_affected": true
}}"""


def build_chat_system_prompt(schedule_context: dict) -> str:
    """System prompt arricchito con il contesto corrente per la chat multi-turno."""
    return f"""{SYSTEM_PROMPT_BASE}

CONTESTO SCHEDULE CORRENTE:
{_ctx_str(schedule_context)}

Quando l'utente chiede di generare un report, rispondi con:
  {{"action_type": "REPORT", "message": "...", "data": {{"report_text": "..."}}}}
Quando l'utente chiede una simulazione, rispondi con:
  {{"action_type": "SIMULATION", "message": "...", "data": {{"impact": "...", "delta_days": 0}}}}
Quando l'utente chiede un suggerimento applicabile, rispondi con:
  {{"action_type": "SUGGESTION", "message": "...", "apply_actions": [...]}}
In tutti gli altri casi rispondi con:
  {{"action_type": "INFO", "message": "..."}}"""


def build_compare_scenarios_prompt(ctx_a: dict, ctx_b: dict, objective: str) -> str:
    """Prompt per confronto tra due scenari."""
    return f"""Confronta i due scenari di schedule e fornisci una raccomandazione chiara.

OBIETTIVO DELL'ANALISI: {objective}

SCENARIO A:
{_ctx_str(ctx_a)}

SCENARIO B:
{_ctx_str(ctx_b)}

Rispondi con JSON strutturato:
{{
  "recommendation": "testo della raccomandazione",
  "delta_summary": "sintesi delle differenze principali",
  "preferred_scenario": "A|B",
  "reasons": ["motivo 1", "motivo 2", "..."]
}}"""


def build_history_analysis_prompt(historical_data: dict) -> str:
    """Prompt per analisi dei pattern storici tra scenari."""
    return f"""Analizza i dati storici di scheduling e identifica pattern ricorrenti,
criticità sistemiche e opportunità di miglioramento.

DATI STORICI:
{_ctx_str(historical_data)}

Rispondi con JSON strutturato:
{{
  "patterns": ["pattern 1", "pattern 2"],
  "critical_issues": ["criticità 1", "criticità 2"],
  "recommendations": ["raccomandazione 1", "raccomandazione 2"],
  "summary": "sintesi complessiva"
}}"""


def build_explain_entry_prompt(entry_context: dict) -> str:
    """Prompt per spiegare perché una schedule entry è stata posizionata così."""
    return f"""Spiega in modo chiaro e conciso perché questa operazione è stata schedulata
in questo slot temporaneo, con questo operatore.

DETTAGLI ENTRY:
{_ctx_str(entry_context)}

Fornisci una spiegazione in testo libero (2-4 frasi) che il planner possa leggere
direttamente. NON usare JSON. Menziona vincoli concreti: turni, precedenze, mancanti."""
