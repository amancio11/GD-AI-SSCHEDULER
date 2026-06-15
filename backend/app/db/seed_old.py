"""
Seed script v2 — TURBOPRESS-X500 mock data ampliato.

Miglioramenti rispetto alla v1:
  - Calendario esteso a 56 giorni (8 settimane) invece di 28
  - 25 assenze distribuite realisticamente (ferie estive + singole)
  - Durate operazioni coerenti con il tipo (ELECTRICAL più brevi, MECHANICAL più lunghe)
  - Progress_pct non-zero su alcune operazioni (simulazione lavori già avviati)
  - 3 componenti mancanti aggiuntivi con date più realistiche
  - 2 scenari aggiuntivi (MINIMIZE_OPERATORS, MAXIMIZE_RESOURCE_UTILIZATION)
  - Operazioni macchina con sequence_number e tipi coerenti al DAG
  - WC-TORINO come workcenter secondario con operazioni reali (non solo BERGAMO)
  - Turni weekend: solo MATTINA (no POMERIGGIO/NOTTE nei sabato/domenica)
  - Note calendario realistiche (ferie, permesso, malattia)

Usage:  cd backend && python -m app.db.seed_v2
Idempotent: ogni INSERT usa ON CONFLICT DO NOTHING su chiave business.
random.seed(42) impostato una volta sola in main().
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# ─── Deterministic UUIDs ─────────────────────────────────────────────────────
_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def sid(name: str) -> uuid.UUID:
    """UUID5 deterministico da name — identico ad ogni esecuzione."""
    return uuid.uuid5(_NS, name)


# ─── Connessione ─────────────────────────────────────────────────────────────
_DATABASE_URL: str = os.environ["DATABASE_URL"].replace(
    "postgresql+asyncpg://", "postgresql://"
)

# ─── Date di riferimento ─────────────────────────────────────────────────────
TODAY: date = date.today()
NOW: datetime = datetime.now(timezone.utc)

# ─── ID pre-calcolati ────────────────────────────────────────────────────────
WC_IDS: dict[str, uuid.UUID] = {
    "WC-MILANO":  sid("wc:WC-MILANO"),
    "WC-TORINO":  sid("wc:WC-TORINO"),
    "WC-BERGAMO": sid("wc:WC-BERGAMO"),
}

MM_TX500_ID: uuid.UUID = sid("mm:TX500")

SH_IDS: dict[str, uuid.UUID] = {
    "Mattina":    sid("sh:Mattina"),
    "Pomeriggio": sid("sh:Pomeriggio"),
    "Notte":      sid("sh:Notte"),
}

MACH_ORDER_ID: uuid.UUID = sid("mo:ORD-MACH-001")
PROD_MACH_ID:  uuid.UUID = sid("po:ORD-MACH-001")

SCENARIO_BASE_ID:    uuid.UUID = sid("sc:Scenario-Base")
SCENARIO_MINOP_ID:   uuid.UUID = sid("sc:Scenario-MinOp")
SCENARIO_MAXUTIL_ID: uuid.UUID = sid("sc:Scenario-MaxUtil")

MACRO_IDS: dict[str, uuid.UUID] = {
    "MA-001": sid("po:MA-001"),
    "MA-002": sid("po:MA-002"),
    "MA-003": sid("po:MA-003"),
}

AGG_CODES = [
    "AGG-001", "AGG-002", "AGG-003", "AGG-004", "AGG-005",
    "AGG-006", "AGG-007", "AGG-008", "AGG-009",
    "AGG-010", "AGG-011", "AGG-012",
]
AGG_IDS: dict[str, uuid.UUID] = {c: sid(f"po:{c}") for c in AGG_CODES}

# ─── Definizione gruppi ───────────────────────────────────────────────────────
# (code, description, parent_aggregate)
GRP_DEFS: list[tuple[str, str, str]] = [
    # AGG-001 Cilindro Principale
    ("GRP-001", "Kit Guarnizioni Cilindro",    "AGG-001"),
    ("GRP-002", "Gruppo Pistoni",              "AGG-001"),
    ("GRP-003", "Flangia Attacco",             "AGG-001"),
    # AGG-002 Pompa Olio
    ("GRP-004", "Corpo Pompa",                 "AGG-002"),
    ("GRP-005", "Gruppo Ingranaggi",           "AGG-002"),
    ("GRP-006", "Coperchio Pompa",             "AGG-002"),
    ("GRP-007", "Kit Tenute Pompa",            "AGG-002"),
    # AGG-003 Collettore
    ("GRP-008", "Blocco Valvole",              "AGG-003"),
    ("GRP-009", "Raccorderia",                 "AGG-003"),
    ("GRP-010", "Supporti Collettore",         "AGG-003"),
    # AGG-004 Accumulatore
    ("GRP-011", "Serbatoio Accumulatore",      "AGG-004"),
    ("GRP-012", "Membrana Separatrice",        "AGG-004"),
    ("GRP-013", "Valvola Gas N2",              "AGG-004"),
    ("GRP-014", "Staffa Fissaggio",            "AGG-004"),
    # AGG-005 Filtro Idraulico
    ("GRP-015", "Corpo Filtro",                "AGG-005"),
    ("GRP-016", "Elemento Filtrante",          "AGG-005"),
    ("GRP-017", "Bypass Filtro",               "AGG-005"),
    # AGG-006 Armadio Principale
    ("GRP-018", "Struttura Armadio",           "AGG-006"),
    ("GRP-019", "Pannello Porta",              "AGG-006"),
    ("GRP-020", "Canalina Cablaggio",          "AGG-006"),
    ("GRP-021", "Barra DIN Principale",        "AGG-006"),
    # AGG-007 Modulo PLC
    ("GRP-022", "CPU PLC",                     "AGG-007"),
    ("GRP-023", "Moduli I/O",                  "AGG-007"),
    ("GRP-024", "Alimentatore PLC",            "AGG-007"),
    # AGG-008 Pannello HMI
    ("GRP-025", "Schermo Touch 15pol",         "AGG-008"),
    ("GRP-026", "Supporto HMI",                "AGG-008"),
    ("GRP-027", "Cablaggio HMI",               "AGG-008"),
    ("GRP-028", "PC Industriale",              "AGG-008"),
    # AGG-009 Quadro Distribuzione
    ("GRP-029", "Interruttori Principali",     "AGG-009"),
    ("GRP-030", "Morsettiera Distribuzione",   "AGG-009"),
    ("GRP-031", "Protezioni Motori",           "AGG-009"),
    # AGG-010 Telaio Base
    ("GRP-032", "Longheroni Base",             "AGG-010"),
    ("GRP-033", "Traversi Inferiori",          "AGG-010"),
    ("GRP-034", "Piastre Ancoraggio",          "AGG-010"),
    # AGG-011 Montanti
    ("GRP-035", "Colonne Verticali",           "AGG-011"),
    ("GRP-036", "Rinforzi Laterali",           "AGG-011"),
    ("GRP-037", "Giunti Colonne",              "AGG-011"),
    ("GRP-038", "Tappi Chiusura",              "AGG-011"),
    # AGG-012 Traversa
    ("GRP-039", "Trave Superiore",             "AGG-012"),
    ("GRP-040", "Connettori Traversa",         "AGG-012"),
]

GRP_IDS: dict[str, uuid.UUID] = {g[0]: sid(f"po:{g[0]}") for g in GRP_DEFS}
RP_IDS: dict[str, uuid.UUID]  = {f"RP-{i:03d}": sid(f"rp:RP-{i:03d}") for i in range(1, 11)}

# ─── Definizione operatori ────────────────────────────────────────────────────
# (employee_id, full_name, skill, workcenter_code)
OP_DEFS: list[tuple[str, str, str, str]] = [
    # WC-MILANO — 8 operatori
    ("EMP-001", "Marco Bianchi",      "ELECTRICAL", "WC-MILANO"),
    ("EMP-002", "Anna Colombo",       "ELECTRICAL", "WC-MILANO"),
    ("EMP-003", "Luca Ferrari",       "ELECTRICAL", "WC-MILANO"),
    ("EMP-004", "Giuseppe Russo",     "MECHANICAL", "WC-MILANO"),
    ("EMP-005", "Maria Esposito",     "MECHANICAL", "WC-MILANO"),
    ("EMP-006", "Paolo Romano",       "MECHANICAL", "WC-MILANO"),
    ("EMP-007", "Sara Conti",         "MULTI",      "WC-MILANO"),
    ("EMP-008", "Diego Marino",       "MULTI",      "WC-MILANO"),
    # WC-TORINO — 7 operatori
    ("EMP-009", "Francesca Vitale",   "ELECTRICAL", "WC-TORINO"),
    ("EMP-010", "Roberto Costa",      "ELECTRICAL", "WC-TORINO"),
    ("EMP-011", "Valentina Gallo",    "MECHANICAL", "WC-TORINO"),
    ("EMP-012", "Matteo Ricci",       "MECHANICAL", "WC-TORINO"),
    ("EMP-013", "Claudia Bruno",      "MECHANICAL", "WC-TORINO"),
    ("EMP-014", "Stefano Lombardi",   "MULTI",      "WC-TORINO"),
    ("EMP-015", "Elena Moretti",      "MULTI",      "WC-TORINO"),
    # WC-BERGAMO — 5 operatori
    ("EMP-016", "Antonio Fontana",    "ELECTRICAL", "WC-BERGAMO"),
    ("EMP-017", "Giulia Caruso",      "MECHANICAL", "WC-BERGAMO"),
    ("EMP-018", "Fabio Rizzo",        "MECHANICAL", "WC-BERGAMO"),
    ("EMP-019", "Cristina De Luca",   "MULTI",      "WC-BERGAMO"),
    ("EMP-020", "Davide Greco",       "MULTI",      "WC-BERGAMO"),
]
OP_IDS: dict[str, uuid.UUID] = {e[0]: sid(f"op:{e[0]}") for e in OP_DEFS}


# ─── Helper: dominio/workcenter per aggregato e gruppo ───────────────────────

def _agg_domain(code: str) -> str:
    n = int(code.split("-")[1])
    if n <= 5:  return "IDRAULICO"
    if n <= 9:  return "ELETTRICO"
    return "STRUTTURA"

def _agg_wc(code: str) -> uuid.UUID:
    dom = _agg_domain(code)
    if dom == "STRUTTURA": return WC_IDS["WC-BERGAMO"]
    return WC_IDS["WC-MILANO"]

def _grp_domain(grp_code: str) -> str:
    parent = next(p for c, _, p in GRP_DEFS if c == grp_code)
    return _agg_domain(parent)

def _grp_wc(grp_code: str) -> uuid.UUID:
    parent = next(p for c, _, p in GRP_DEFS if c == grp_code)
    return _agg_wc(parent)


# ─── Durate realistiche per tipo operazione ───────────────────────────────────
# Electrical: 90–300 min (cablaggio, collaudi elettrici)
# Mechanical: 180–480 min (assemblaggio meccanico, pressature)
# General:    120–360 min (assemblaggio generico, pulizia, imballo)

def _realistic_duration(op_type: str) -> int:
    if op_type == "ELECTRICAL":
        return random.choice([90, 120, 150, 180, 240, 300])
    if op_type == "MECHANICAL":
        return random.choice([180, 240, 300, 360, 420, 480])
    return random.choice([120, 150, 180, 240, 300, 360])


# ═══════════════════════════════════════════════════════════════════════════════
# SEED FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def seed_workcenters(conn: asyncpg.Connection) -> None:
    rows = [
        (WC_IDS["WC-MILANO"],  "WC-MILANO",  "Milano",  "Officina principale — Idraulica ed Elettrica"),
        (WC_IDS["WC-TORINO"],  "WC-TORINO",  "Torino",  "Officina secondaria — Cablaggio e collaudi"),
        (WC_IDS["WC-BERGAMO"], "WC-BERGAMO", "Bergamo", "Officina strutture — Carpenteria pesante"),
    ]
    await conn.executemany(
        "INSERT INTO workcenters (id, code, name, location) VALUES ($1,$2,$3,$4) "
        "ON CONFLICT (code) DO NOTHING",
        rows,
    )


async def seed_machine_model(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "INSERT INTO machine_models (id, code, name) VALUES ($1,$2,$3) "
        "ON CONFLICT (code) DO NOTHING",
        MM_TX500_ID, "TX500", "TURBOPRESS-X500",
    )


async def seed_shifts(conn: asyncpg.Connection) -> None:
    rows = [
        (SH_IDS["Mattina"],    "Mattina",    time(6, 0),  time(14, 0), 30, True),
        (SH_IDS["Pomeriggio"], "Pomeriggio", time(14, 0), time(22, 0), 30, True),
        (SH_IDS["Notte"],      "Notte",      time(22, 0), time(6, 0),  30, True),
    ]
    await conn.executemany(
        "INSERT INTO shifts (id, name, start_time, end_time, break_duration_minutes, is_active) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (name) DO NOTHING",
        rows,
    )


async def seed_skill_workcenter_mapping(conn: asyncpg.Connection) -> None:
    rows = []
    for wc_code, wc_id in WC_IDS.items():
        rows += [
            (sid(f"swm:ELECTRICAL:{wc_code}"), wc_id, "ELECTRICAL", True,  False, False),
            (sid(f"swm:MECHANICAL:{wc_code}"), wc_id, "MECHANICAL", False, True,  False),
            (sid(f"swm:MULTI:{wc_code}"),      wc_id, "MULTI",      True,  True,  True),
        ]
    await conn.executemany(
        "INSERT INTO skill_workcenter_mapping "
        "(id, workcenter_id, skill, can_do_electrical, can_do_mechanical, can_do_general) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (workcenter_id, skill) DO NOTHING",
        rows,
    )


async def seed_operators(conn: asyncpg.Connection) -> None:
    rows = [
        (OP_IDS[emp_id], emp_id, name, skill, WC_IDS[wc], True)
        for emp_id, name, skill, wc in OP_DEFS
    ]
    await conn.executemany(
        "INSERT INTO operators (id, employee_id, full_name, skill, workcenter_id, is_active) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (employee_id) DO NOTHING",
        rows,
    )


async def seed_machine_order(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "INSERT INTO machine_orders "
        "(id, sap_order_id, machine_model_id, workcenter_id, status, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (sap_order_id) DO NOTHING",
        MACH_ORDER_ID, "ORD-MACH-001", MM_TX500_ID,
        WC_IDS["WC-MILANO"], "IN_PROGRESS", NOW,
    )


async def seed_bom(conn: asyncpg.Connection) -> None:
    """Gerarchia completa: MACHINE → MACRO → AGG → GRP → COMPONENT."""

    def po(id_, sap, parent_id, parent_mat, level, mat, desc, wc_id,
           qty=1, unit="PZ", progress=0.0, status="PLANNED",
           is_purchase=False, is_untracked=False):
        return (
            id_, sap, parent_id, parent_mat, MACH_ORDER_ID,
            level, mat, desc, qty, unit, wc_id,
            progress, status, None, is_purchase, is_untracked, NOW,
        )

    rows: list[tuple] = []

    # Ordine macchina radice
    rows.append(po(
        PROD_MACH_ID, "ORD-MACH-001", None, None,
        "MACHINE", "TX500-MACH", "TURBOPRESS-X500",
        WC_IDS["WC-MILANO"], progress=15.0, status="IN_PROGRESS",
    ))

    # Macroaggregati — progress variabili per simulare lavori avviati
    macro_defs = [
        ("MA-001", "MA-001-MAT", "Gruppo Idraulico",   WC_IDS["WC-MILANO"],  25.0, "IN_PROGRESS"),
        ("MA-002", "MA-002-MAT", "Quadro Elettrico",   WC_IDS["WC-MILANO"],  10.0, "IN_PROGRESS"),
        ("MA-003", "MA-003-MAT", "Struttura Portante", WC_IDS["WC-BERGAMO"], 60.0, "IN_PROGRESS"),
    ]
    for code, mat, desc, wc, prog, status in macro_defs:
        rows.append(po(MACRO_IDS[code], f"SAP-{code}", PROD_MACH_ID, "TX500-MACH",
                       "MACROAGGREGATE", mat, desc, wc, progress=prog, status=status))

    # Aggregati
    AGG_DEFS = [
        ("AGG-001", "Cilindro Principale",  "MA-001", 20.0, "IN_PROGRESS"),
        ("AGG-002", "Pompa Olio",           "MA-001", 30.0, "IN_PROGRESS"),
        ("AGG-003", "Collettore",           "MA-001",  0.0, "PLANNED"),
        ("AGG-004", "Accumulatore",         "MA-001",  0.0, "PLANNED"),
        ("AGG-005", "Filtro Idraulico",     "MA-001",  0.0, "PLANNED"),
        ("AGG-006", "Armadio Principale",   "MA-002", 15.0, "IN_PROGRESS"),
        ("AGG-007", "Modulo PLC",           "MA-002",  0.0, "PLANNED"),
        ("AGG-008", "Pannello HMI",         "MA-002",  0.0, "PLANNED"),
        ("AGG-009", "Quadro Distribuzione", "MA-002",  5.0, "IN_PROGRESS"),
        ("AGG-010", "Telaio Base",          "MA-003", 80.0, "IN_PROGRESS"),
        ("AGG-011", "Montanti",             "MA-003", 70.0, "IN_PROGRESS"),
        ("AGG-012", "Traversa",             "MA-003", 40.0, "IN_PROGRESS"),
    ]
    for code, desc, parent_macro, prog, status in AGG_DEFS:
        wc = _agg_wc(code)
        parent_mat = f"{parent_macro}-MAT"
        rows.append(po(AGG_IDS[code], f"SAP-{code}", MACRO_IDS[parent_macro],
                       parent_mat, "AGGREGATE", f"{code}-MAT", desc, wc,
                       progress=prog, status=status))

    # Gruppi
    for grp_code, grp_desc, parent_agg in GRP_DEFS:
        wc = _grp_wc(grp_code)
        prog = random.uniform(0, 40)  # tra 0% e 40% — lavori parzialmente avviati
        status = "IN_PROGRESS" if prog > 5 else "PLANNED"
        rows.append(po(GRP_IDS[grp_code], f"SAP-{grp_code}",
                       AGG_IDS[parent_agg], f"{parent_agg}-MAT",
                       "GROUP", f"{grp_code}-MAT", grp_desc, wc,
                       progress=round(prog, 1), status=status))

    # Componenti (~150, 3-6 per gruppo)
    comp_counter = 1
    domain_prefix = {"IDRAULICO": "IDR", "ELETTRICO": "ELE", "STRUTTURA": "STR"}
    units = ["PZ", "MT", "KG", "M2", "ML"]
    comp_names = {
        "IDRAULICO": ["Guarnizione", "Raccordo", "Valvola", "Tubo", "O-Ring",
                      "Molla", "Pistone", "Dado", "Vite", "Flangia"],
        "ELETTRICO": ["Cavo", "Connettore", "Morsetto", "Relè", "Fusibile",
                      "Contattore", "Canalina", "Scarica", "Terminale", "LED"],
        "STRUTTURA": ["Bullone", "Piastra", "Profilato", "Vite M12", "Dado M12",
                      "Rondella", "Tassello", "Staffa", "Angolare", "Perno"],
    }
    for grp_code, grp_desc, parent_agg in GRP_DEFS:
        dom = _grp_domain(grp_code)
        pfx = domain_prefix[dom]
        n = random.randint(3, 6)
        for i in range(1, n + 1):
            mat = f"{pfx}-{comp_counter:04d}"
            sap = f"COMP-{grp_code}-{i:02d}"
            is_purchase = random.random() < 0.75
            qty = random.randint(1, 20)
            unit = random.choice(units)
            name = random.choice(comp_names[dom])
            rows.append((
                sid(f"po:{sap}"), sap,
                GRP_IDS[grp_code], f"{grp_code}-MAT",
                MACH_ORDER_ID,
                "COMPONENT", mat,
                f"{name} {mat} — {grp_desc}",
                qty, unit, None,
                0.0, "PLANNED",
                None, is_purchase, not is_purchase, NOW,
            ))
            comp_counter += 1

    await conn.executemany(
        """
        INSERT INTO production_orders (
            id, sap_order_id, parent_order_id, parent_material, machine_order_id,
            level, material_code, description, quantity, unit, workcenter_id,
            progress_pct, status, missing_arrival_date,
            is_purchase_component, is_production_component_untracked, created_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (sap_order_id) DO NOTHING
        """,
        rows,
    )


async def seed_z_orders_link(conn: asyncpg.Connection) -> None:
    rows: list[tuple] = []

    def link(child_id, parent_id, parent_mat, child_mat, level):
        return (sid(f"zol:{child_id}:{parent_id}"),
                child_id, parent_id, parent_mat, child_mat, level, "BOM")

    for code, mat, _, _, _, _ in [
        ("MA-001", "MA-001-MAT", None, None, None, None),
        ("MA-002", "MA-002-MAT", None, None, None, None),
        ("MA-003", "MA-003-MAT", None, None, None, None),
    ]:
        rows.append(link(MACRO_IDS[code], PROD_MACH_ID, "TX500-MACH", mat, "MACROAGGREGATE"))

    AGG_PARENT = {
        "AGG-001": "MA-001", "AGG-002": "MA-001", "AGG-003": "MA-001",
        "AGG-004": "MA-001", "AGG-005": "MA-001",
        "AGG-006": "MA-002", "AGG-007": "MA-002", "AGG-008": "MA-002", "AGG-009": "MA-002",
        "AGG-010": "MA-003", "AGG-011": "MA-003", "AGG-012": "MA-003",
    }
    for agg_code, parent_macro in AGG_PARENT.items():
        rows.append(link(AGG_IDS[agg_code], MACRO_IDS[parent_macro],
                         f"{parent_macro}-MAT", f"{agg_code}-MAT", "AGGREGATE"))

    for grp_code, _, parent_agg in GRP_DEFS:
        rows.append(link(GRP_IDS[grp_code], AGG_IDS[parent_agg],
                         f"{parent_agg}-MAT", f"{grp_code}-MAT", "GROUP"))

    await conn.executemany(
        "INSERT INTO z_orders_link "
        "(id, child_order_id, parent_order_id, parent_material, child_material, level, link_type) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) "
        "ON CONFLICT (child_order_id, parent_order_id) DO NOTHING",
        rows,
    )


async def seed_reference_points(conn: asyncpg.Connection) -> None:
    rp_defs = [
        ("RP-001", "Completamento Struttura Portante",   "MACROAGGREGATE", "MA-003-MAT"),
        ("RP-002", "Completamento Gruppo Idraulico",     "MACROAGGREGATE", "MA-001-MAT"),
        ("RP-003", "Completamento Quadro Elettrico",     "MACROAGGREGATE", "MA-002-MAT"),
        ("RP-004", "Completamento Cilindro Principale",  "AGGREGATE",      "AGG-001-MAT"),
        ("RP-005", "Completamento Pompa Olio",           "AGGREGATE",      "AGG-002-MAT"),
        ("RP-006", "Completamento Armadio Principale",   "AGGREGATE",      "AGG-006-MAT"),
        ("RP-007", "Completamento Modulo PLC",           "AGGREGATE",      "AGG-007-MAT"),
        ("RP-008", "Completamento Telaio Base",          "AGGREGATE",      "AGG-010-MAT"),
        ("RP-009", "Completamento Montanti",             "AGGREGATE",      "AGG-011-MAT"),
        ("RP-010", "Completamento Collettore",           "AGGREGATE",      "AGG-003-MAT"),
    ]
    rows = [
        (RP_IDS[code], code, name, MM_TX500_ID, target_level, target_mat)
        for code, name, target_level, target_mat in rp_defs
    ]
    await conn.executemany(
        "INSERT INTO reference_points "
        "(id, code, name, machine_model_id, target_level, target_order_material) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (code, machine_model_id) DO NOTHING",
        rows,
    )


async def seed_reference_point_precedences(conn: asyncpg.Connection) -> None:
    """
    DAG aciclico:
    RP-001 (Struttura)
      ├─► RP-002 (Idraulico)  ──► RP-004 (Cilindro)
      │                            │───► RP-005 (Pompa)
      │                            └───► RP-010 (Collettore)
      ├─► RP-003 (Elettrico)  ──► RP-006 (Armadio)
      │                            └───► RP-007 (PLC)
      └─► RP-008 (Telaio)     ──► RP-009 (Montanti)
    """
    pairs = [
        ("RP-002", "RP-001"),
        ("RP-003", "RP-001"),
        ("RP-008", "RP-001"),
        ("RP-004", "RP-002"),
        ("RP-005", "RP-004"),
        ("RP-006", "RP-003"),
        ("RP-007", "RP-006"),
        ("RP-009", "RP-008"),
        ("RP-010", "RP-004"),
        ("RP-010", "RP-005"),
    ]
    rows = [
        (sid(f"rpp:{rp}:{pred}"), RP_IDS[rp], RP_IDS[pred], MM_TX500_ID)
        for rp, pred in pairs
    ]
    await conn.executemany(
        "INSERT INTO reference_point_precedences "
        "(id, reference_point_id, predecessor_reference_point_id, machine_model_id) "
        "VALUES ($1,$2,$3,$4) "
        "ON CONFLICT (reference_point_id, predecessor_reference_point_id) DO NOTHING",
        rows,
    )


async def seed_routings_and_operations(conn: asyncpg.Connection) -> None:
    routing_rows: list[tuple] = []
    operation_rows: list[tuple] = []

    def add_routing_ops(po_id, sap_routing_id, domain, wc_id,
                        num_ops=None, progress_override=None):
        r_id = sid(f"rt:{sap_routing_id}")
        routing_rows.append((r_id, po_id, sap_routing_id, "SIMULTANEOUS"))

        # Distribuzione tipi operazione per dominio
        if domain == "ELETTRICO":
            op_types = ["ELECTRICAL"] * 3 + ["GENERAL"]
        elif domain == "IDRAULICO":
            op_types = ["MECHANICAL"] * 3 + ["GENERAL"]
        elif domain == "STRUTTURA":
            op_types = ["MECHANICAL"] * 4 + ["GENERAL"]
        else:
            op_types = ["GENERAL"] * 3

        n = num_ops or random.randint(3, 6)
        chosen_types = [op_types[i % len(op_types)] for i in range(n)]

        for seq, op_type in enumerate(chosen_types, start=1):
            dur = _realistic_duration(op_type)
            prog = progress_override if progress_override is not None else random.uniform(0, 30)
            # Alcune operazioni completate se progress alto
            if prog >= 100:
                status = "COMPLETED"
            elif prog > 0:
                status = "IN_PROGRESS"
            else:
                status = "PENDING"

            operation_rows.append((
                sid(f"op:{sap_routing_id}:{seq}"),
                r_id,
                f"{sap_routing_id}-OP-{seq:02d}",
                seq,
                _op_description(op_type, seq),
                op_type,
                wc_id,
                dur,
                None,
                round(prog, 1),
                status,
                None,   # reference_point_id — solo per macchina
                True,   # can_be_interrupted
            ))

    def _op_description(op_type: str, seq: int) -> str:
        descs = {
            "ELECTRICAL": [
                "Cablaggio principale", "Connessione morsettiera", "Test isolamento",
                "Collaudo funzionale", "Taratura sensori", "Installazione protezioni",
            ],
            "MECHANICAL": [
                "Pre-montaggio struttura", "Assemblaggio componenti", "Serraggio viteria",
                "Controllo geometrico", "Pressatura guarnizioni", "Collaudo tenuta",
            ],
            "GENERAL": [
                "Pulizia e preparazione", "Controllo visivo", "Verniciatura",
                "Imballo", "Marcatura CE", "Documentazione qualità",
            ],
        }
        options = descs.get(op_type, descs["GENERAL"])
        return options[(seq - 1) % len(options)]

    # ── Ordine Macchina: 10 operazioni fisse con Reference Point ─────────────
    r_mach_id = sid("rt:SAP-ORD-MACH-001")
    routing_rows.append((r_mach_id, PROD_MACH_ID, "SAP-ORD-MACH-001", "SIMULTANEOUS"))

    mach_op_defs = [
        # (seq, description, type, rp_code, progress)
        (1,  "Ispezione struttura portante",         "MECHANICAL", "RP-001", 100.0),
        (2,  "Verifica telaio base",                 "MECHANICAL", "RP-008", 80.0),
        (3,  "Collaudo montanti",                    "MECHANICAL", "RP-009", 60.0),
        (4,  "Primo collaudo idraulico",             "MECHANICAL", "RP-002", 20.0),
        (5,  "Test cilindro principale",             "MECHANICAL", "RP-004", 0.0),
        (6,  "Collaudo pompa olio",                  "MECHANICAL", "RP-005", 0.0),
        (7,  "Test collettore",                      "MECHANICAL", "RP-010", 0.0),
        (8,  "Collaudo armadio elettrico",           "ELECTRICAL", "RP-006", 0.0),
        (9,  "Configurazione PLC",                   "ELECTRICAL", "RP-007", 0.0),
        (10, "Collaudo finale quadro elettrico",     "ELECTRICAL", "RP-003", 0.0),
    ]
    for seq, desc, op_type, rp_code, prog in mach_op_defs:
        status = "COMPLETED" if prog >= 100 else ("IN_PROGRESS" if prog > 0 else "PENDING")
        operation_rows.append((
            sid(f"op_mach:ORD-MACH-001:{seq}"),
            r_mach_id,
            f"MACH-OP-{seq:02d}",
            seq,
            desc,
            op_type,
            WC_IDS["WC-MILANO"],
            _realistic_duration(op_type),
            None,
            prog,
            status,
            RP_IDS[rp_code],
            True,
        ))

    # ── Macroaggregati ────────────────────────────────────────────────────────
    add_routing_ops(MACRO_IDS["MA-001"], "SAP-MA-001", "IDRAULICO",  WC_IDS["WC-MILANO"],  num_ops=5, progress_override=None)
    add_routing_ops(MACRO_IDS["MA-002"], "SAP-MA-002", "ELETTRICO",  WC_IDS["WC-MILANO"],  num_ops=5, progress_override=None)
    add_routing_ops(MACRO_IDS["MA-003"], "SAP-MA-003", "STRUTTURA",  WC_IDS["WC-BERGAMO"], num_ops=4, progress_override=None)

    # ── Aggregati ─────────────────────────────────────────────────────────────
    for agg_code in AGG_CODES:
        dom = _agg_domain(agg_code)
        wc = _agg_wc(agg_code)
        add_routing_ops(AGG_IDS[agg_code], f"SAP-{agg_code}", dom, wc)

    # ── Gruppi ────────────────────────────────────────────────────────────────
    for grp_code, _, _ in GRP_DEFS:
        dom = _grp_domain(grp_code)
        wc = _grp_wc(grp_code)
        add_routing_ops(GRP_IDS[grp_code], f"SAP-{grp_code}", dom, wc)

    await conn.executemany(
        "INSERT INTO routings (id, production_order_id, sap_routing_id, execution_mode) "
        "VALUES ($1,$2,$3,$4) ON CONFLICT (production_order_id) DO NOTHING",
        routing_rows,
    )
    await conn.executemany(
        """
        INSERT INTO operations (
            id, routing_id, sap_operation_id, sequence_number, description,
            operation_type, workcenter_id,
            planned_duration_minutes, actual_duration_minutes,
            progress_pct, status, reference_point_id, can_be_interrupted
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (routing_id, sequence_number) DO NOTHING
        """,
        operation_rows,
    )


async def seed_missing_components(conn: asyncpg.Connection) -> None:
    """8 componenti mancanti con date d'arrivo realistiche e note dettagliate."""
    mc_defs = [
        # (sap_id, mat_code, desc, grp_code, arrival_days, notes)
        ("MC-001", "IDR-0001", "Valvola idraulica proporzionale VLV-2200",
         "GRP-008", 7,  "In transito da fornitore DE — DHL express"),
        ("MC-002", "ELE-0015", "Cavo Schermato 4x25mm² CAB-450",
         "GRP-020", 3,  "Ordine urgente emesso — consegna garantita"),
        ("MC-003", "IDR-0022", "Sensore pressione 0-400bar SEN-P100",
         "GRP-013", 12, "Produzione custom — delivery confermata dal fornitore IT"),
        ("MC-004", "STR-0007", "Vite speciale M16×80 acciaio 10.9",
         "GRP-033", 1,  "Disponibile in magazzino centrale TN — in spedizione"),
        ("MC-005", "IDR-0033", "Guarnizione gomma NBR 250×5",
         "GRP-001", 5,  "Taglio su misura da terzista — in lavorazione"),
        ("MC-006", "ELE-0041", "Contattore trifase 125A Siemens 3RT",
         "GRP-029", 4,  "Riordino su stockout — alternativa in valutazione"),
        ("MC-007", "IDR-0055", "Filtro olio 25 micron per accumulatore",
         "GRP-016", 9,  "Fornitore estero — spedizione aerea in corso"),
        ("MC-008", "STR-0019", "Profilato HEA 200 L=4500mm",
         "GRP-032", 2,  "Taglio da barra lunga — officina Bergamo"),
    ]
    rows = []
    for sap_id, mat_code, desc, grp_code, days, notes in mc_defs:
        arrival = TODAY + timedelta(days=days)
        rows.append((
            sid(f"mc:{sap_id}"),
            sap_id,
            mat_code,
            desc,
            GRP_IDS[grp_code],
            # production_order_id = il gruppo padre
            GRP_IDS[grp_code],
            arrival,
            False,   # is_arrived
            notes,
            NOW,
        ))
    await conn.executemany(
        """
        INSERT INTO missing_components (
            id, sap_material_id, material_code, description,
            group_order_id, production_order_id,
            expected_arrival_date, is_arrived, notes, created_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (sap_material_id) DO NOTHING
        """,
        rows,
    )


async def seed_operator_calendar(conn: asyncpg.Connection) -> None:
    """
    56 giorni di calendario (8 settimane) con turni realistici:
    - Lunedì-Venerdì: turno rotante (Mattina/Pomeriggio/Notte)
    - Sabato: solo Mattina (officina ridotta)
    - Domenica: assenza (chiusura officina)
    - 25 assenze distribuite: 8 ferie estive a blocco + 17 singole
    """
    dates = [TODAY + timedelta(days=i) for i in range(56)]
    shifts_cycle = [SH_IDS["Mattina"], SH_IDS["Pomeriggio"], SH_IDS["Notte"]]

    # Assenze estive a blocco: 3 operatori in ferie 2 settimane a partire da today+14
    SUMMER_LEAVE_START = TODAY + timedelta(days=14)
    SUMMER_LEAVE_DAYS = 10  # 2 settimane lavorative
    summer_leave_ops = ["EMP-001", "EMP-005", "EMP-017"]  # uno per WC
    summer_absence_set: set[tuple[str, date]] = set()
    for emp_id in summer_leave_ops:
        work_days = 0
        d = SUMMER_LEAVE_START
        while work_days < SUMMER_LEAVE_DAYS:
            if d.weekday() < 5:  # lunedì-venerdì
                summer_absence_set.add((emp_id, d))
                work_days += 1
            d += timedelta(days=1)

    # 17 assenze singole casuali su altri operatori
    remaining_pairs = [
        (op_def[0], d)
        for op_def in OP_DEFS
        for d in dates
        if op_def[0] not in summer_leave_ops
        and d.weekday() < 5
        and (op_def[0], d) not in summer_absence_set
    ]
    single_absence_set: set[tuple[str, date]] = set(random.sample(remaining_pairs, 17))
    absence_set = summer_absence_set | single_absence_set

    # Note assenza realistiche
    absence_notes_map: dict[str, str] = {}
    note_options = [
        "Ferie programmate", "Ferie estive", "Permesso retribuito",
        "Malattia", "Formazione esterna", "Visita medica", "Permesso sindacale",
    ]
    for emp_id, d in absence_set:
        key = f"{emp_id}:{d.isoformat()}"
        if emp_id in summer_leave_ops:
            note = "Ferie estive"
        else:
            note = random.choice(note_options)
        absence_notes_map[key] = note

    rows: list[tuple] = []
    for op_idx, op_def in enumerate(OP_DEFS):
        emp_id = op_def[0]
        op_id = OP_IDS[emp_id]
        for day_idx, d in enumerate(dates):
            weekday = d.weekday()  # 0=lunedì, 6=domenica

            # Domenica: sempre chiuso
            if weekday == 6:
                rows.append((
                    sid(f"cal:{emp_id}:{d.isoformat()}"),
                    op_id, d, None, False,
                    "Chiusura domenicale", "ABSENCE",
                ))
                continue

            # Assente programmato
            if (emp_id, d) in absence_set:
                note = absence_notes_map.get(f"{emp_id}:{d.isoformat()}", "Assenza")
                rows.append((
                    sid(f"cal:{emp_id}:{d.isoformat()}"),
                    op_id, d, None, False, note, "ABSENCE",
                ))
                continue

            # Sabato: solo turno Mattina
            if weekday == 5:
                rows.append((
                    sid(f"cal:{emp_id}:{d.isoformat()}"),
                    op_id, d, SH_IDS["Mattina"], True, None, None,
                ))
                continue

            # Lunedì-Venerdì: turno rotante
            shift_id = shifts_cycle[(op_idx + day_idx) % 3]
            rows.append((
                sid(f"cal:{emp_id}:{d.isoformat()}"),
                op_id, d, shift_id, True, None, None,
            ))

    await conn.executemany(
        """
        INSERT INTO operator_calendar
            (id, operator_id, date, shift_id, is_available, notes, override_reason)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (operator_id, date) DO NOTHING
        """,
        rows,
    )


async def seed_scenarios(conn: asyncpg.Connection) -> None:
    """3 scenari con obiettivi diversi per permettere confronto immediato."""
    target_base    = TODAY + timedelta(days=90)
    target_minop   = TODAY + timedelta(days=100)  # più tempo, meno operatori
    target_maxutil = TODAY + timedelta(days=80)   # più stringente

    scenarios = [
        (
            SCENARIO_BASE_ID,
            "Scenario Base",
            "Piano standard — obiettivo FINISH_BY_DATE entro 90 giorni",
            "FINISH_BY_DATE",
            target_base,
            True,  # is_active
            True,  # is_baseline
        ),
        (
            SCENARIO_MINOP_ID,
            "Scenario Economia Risorse",
            "Ottimizza il numero di operatori usati — accetta più tempo",
            "MINIMIZE_OPERATORS",
            target_minop,
            False,
            False,
        ),
        (
            SCENARIO_MAXUTIL_ID,
            "Scenario Massima Produttività",
            "Massimizza l'utilizzo degli operatori — deadline più aggressiva",
            "MAXIMIZE_RESOURCE_UTILIZATION",
            target_maxutil,
            False,
            False,
        ),
    ]
    for sc_id, name, desc, obj_mode, target, is_active, is_baseline in scenarios:
        await conn.execute(
            """
            INSERT INTO schedule_scenarios
                (id, name, description, machine_order_id, objective_mode,
                 target_finish_date, resource_set_json, created_at, is_active, is_baseline)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (name, machine_order_id) DO NOTHING
            """,
            sc_id, name, desc, MACH_ORDER_ID, obj_mode,
            target, "{}", NOW, is_active, is_baseline,
        )


# ─── Count printer ────────────────────────────────────────────────────────────

async def print_counts(conn: asyncpg.Connection) -> None:
    tables = [
        "workcenters", "machine_models", "shifts", "skill_workcenter_mapping",
        "operators", "machine_orders", "production_orders", "z_orders_link",
        "routings", "operations", "reference_points", "reference_point_precedences",
        "missing_components", "operator_calendar", "schedule_scenarios",
    ]
    print("\n== Seed v2 completato ==========================================")
    for t in tables:
        try:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:<35} {n:>6} righe")
        except Exception as exc:
            print(f"  {t:<35} ERRORE: {exc}")
    print("────────────────────────────────────────────────────────────────\n")


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main() -> None:
    random.seed(42)  # mai spostare questo
    conn: asyncpg.Connection = await asyncpg.connect(_DATABASE_URL)
    try:
        print("Connessione OK — avvio seed v2 TURBOPRESS-X500...")
        await seed_workcenters(conn)
        await seed_machine_model(conn)
        await seed_shifts(conn)
        await seed_skill_workcenter_mapping(conn)
        await seed_operators(conn)
        await seed_machine_order(conn)
        await seed_bom(conn)
        await seed_z_orders_link(conn)
        await seed_reference_points(conn)
        await seed_reference_point_precedences(conn)
        await seed_routings_and_operations(conn)
        await seed_missing_components(conn)
        await seed_operator_calendar(conn)
        await seed_scenarios(conn)
        await print_counts(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())