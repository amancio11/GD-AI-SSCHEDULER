"""
Seed script — TURBOPRESS-X500 complete mock data.
Usage:  cd backend && python -m app.db.seed
Idempotent: every INSERT uses ON CONFLICT DO NOTHING on a business-key.
random.seed(42) is set once at the top of main() and never changed.

REFERENCE POINT STRUCTURE (v2 — corrected):
  Each non-component order has RPs pointing ONLY to its direct BOM children.
  - MACHINE level  → 3 RPs → {MA-001, MA-002, MA-003}
  - MA-001 level   → 5 RPs → {AGG-001..005}
  - MA-002 level   → 4 RPs → {AGG-006..009}
  - MA-003 level   → 3 RPs → {AGG-010..012}
  - Each AGG level → N RPs → their direct group children
  - GROUP level    → no RPs (children are purchase components)
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
    """Return a deterministic UUID5 from *name* — identical across every run."""
    return uuid.uuid5(_NS, name)


# ─── Connection ──────────────────────────────────────────────────────────────
_DATABASE_URL: str = os.environ["DATABASE_URL"].replace(
    "postgresql+asyncpg://", "postgresql://"
)

# ─── Reference date ──────────────────────────────────────────────────────────
TODAY: date = date.today()
NOW: datetime = datetime.now(timezone.utc)

# ─── Pre-computed IDs ────────────────────────────────────────────────────────
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
SCENARIO_ID:   uuid.UUID = sid("sc:Scenario-Base")

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

# (code, description, parent_aggregate)
GRP_DEFS: list[tuple[str, str, str]] = [
    ("GRP-001", "Kit Guarnizioni Cilindro",  "AGG-001"),
    ("GRP-002", "Gruppo Pistoni",            "AGG-001"),
    ("GRP-003", "Flangia Attacco",           "AGG-001"),
    ("GRP-004", "Corpo Pompa",               "AGG-002"),
    ("GRP-005", "Gruppo Ingranaggi",         "AGG-002"),
    ("GRP-006", "Coperchio Pompa",           "AGG-002"),
    ("GRP-007", "Kit Tenute Pompa",          "AGG-002"),
    ("GRP-008", "Blocco Valvole",            "AGG-003"),
    ("GRP-009", "Raccorderia",               "AGG-003"),
    ("GRP-010", "Supporti Collettore",       "AGG-003"),
    ("GRP-011", "Serbatoio Accumulatore",    "AGG-004"),
    ("GRP-012", "Membrana Separatrice",      "AGG-004"),
    ("GRP-013", "Valvola Gas N2",            "AGG-004"),
    ("GRP-014", "Staffa Fissaggio",          "AGG-004"),
    ("GRP-015", "Corpo Filtro",              "AGG-005"),
    ("GRP-016", "Elemento Filtrante",        "AGG-005"),
    ("GRP-017", "Bypass Filtro",             "AGG-005"),
    ("GRP-018", "Struttura Armadio",         "AGG-006"),
    ("GRP-019", "Pannello Porta",            "AGG-006"),
    ("GRP-020", "Canalina Cablaggio",        "AGG-006"),
    ("GRP-021", "Barra DIN Principale",      "AGG-006"),
    ("GRP-022", "CPU PLC",                   "AGG-007"),
    ("GRP-023", "Moduli I/O",                "AGG-007"),
    ("GRP-024", "Alimentatore PLC",          "AGG-007"),
    ("GRP-025", "Schermo Touch 15pol",       "AGG-008"),
    ("GRP-026", "Supporto HMI",              "AGG-008"),
    ("GRP-027", "Cablaggio HMI",             "AGG-008"),
    ("GRP-028", "PC Industriale",            "AGG-008"),
    ("GRP-029", "Interruttori Principali",   "AGG-009"),
    ("GRP-030", "Morsettiera Distribuzione", "AGG-009"),
    ("GRP-031", "Protezioni Motori",         "AGG-009"),
    ("GRP-032", "Longheroni Base",           "AGG-010"),
    ("GRP-033", "Traversi Inferiori",        "AGG-010"),
    ("GRP-034", "Piastre Ancoraggio",        "AGG-010"),
    ("GRP-035", "Colonne Verticali",         "AGG-011"),
    ("GRP-036", "Rinforzi Laterali",         "AGG-011"),
    ("GRP-037", "Giunti Colonne",            "AGG-011"),
    ("GRP-038", "Tappi Chiusura",            "AGG-011"),
    ("GRP-039", "Trave Superiore",           "AGG-012"),
    ("GRP-040", "Connettori Traversa",       "AGG-012"),
]
GRP_IDS: dict[str, uuid.UUID] = {g[0]: sid(f"po:{g[0]}") for g in GRP_DEFS}

# ── Reference Point IDs (v2 — one set per BOM level) ─────────────────────────
# Naming: RP-M-xx (machine), RP-MAx-xx (macro), RP-Axxx-xx (aggregate)
_ALL_RP_CODES: list[str] = [
    # Machine level (3)
    "RP-M-01", "RP-M-02", "RP-M-03",
    # MA-001 level (5)
    "RP-MA1-01", "RP-MA1-02", "RP-MA1-03", "RP-MA1-04", "RP-MA1-05",
    # MA-002 level (4)
    "RP-MA2-01", "RP-MA2-02", "RP-MA2-03", "RP-MA2-04",
    # MA-003 level (3)
    "RP-MA3-01", "RP-MA3-02", "RP-MA3-03",
    # AGG-001 level (3)
    "RP-A001-01", "RP-A001-02", "RP-A001-03",
    # AGG-002 level (4)
    "RP-A002-01", "RP-A002-02", "RP-A002-03", "RP-A002-04",
    # AGG-003 level (3)
    "RP-A003-01", "RP-A003-02", "RP-A003-03",
    # AGG-004 level (4)
    "RP-A004-01", "RP-A004-02", "RP-A004-03", "RP-A004-04",
    # AGG-005 level (3)
    "RP-A005-01", "RP-A005-02", "RP-A005-03",
    # AGG-006 level (4)
    "RP-A006-01", "RP-A006-02", "RP-A006-03", "RP-A006-04",
    # AGG-007 level (3)
    "RP-A007-01", "RP-A007-02", "RP-A007-03",
    # AGG-008 level (4)
    "RP-A008-01", "RP-A008-02", "RP-A008-03", "RP-A008-04",
    # AGG-009 level (3)
    "RP-A009-01", "RP-A009-02", "RP-A009-03",
    # AGG-010 level (3)
    "RP-A010-01", "RP-A010-02", "RP-A010-03",
    # AGG-011 level (4)
    "RP-A011-01", "RP-A011-02", "RP-A011-03", "RP-A011-04",
    # AGG-012 level (2)
    "RP-A012-01", "RP-A012-02",
]
RP_IDS: dict[str, uuid.UUID] = {code: sid(f"rp:{code}") for code in _ALL_RP_CODES}

# (employee_id, full_name, skill, workcenter_code)
OP_DEFS: list[tuple[str, str, str, str]] = [
    ("EMP-001", "Marco Bianchi",     "ELECTRICAL", "WC-MILANO"),
    ("EMP-002", "Anna Colombo",      "ELECTRICAL", "WC-MILANO"),
    ("EMP-003", "Luca Ferrari",      "ELECTRICAL", "WC-MILANO"),
    ("EMP-004", "Giuseppe Russo",    "MECHANICAL", "WC-MILANO"),
    ("EMP-005", "Maria Esposito",    "MECHANICAL", "WC-MILANO"),
    ("EMP-006", "Paolo Romano",      "MECHANICAL", "WC-MILANO"),
    ("EMP-007", "Sara Conti",        "MULTI",      "WC-MILANO"),
    ("EMP-008", "Diego Marino",      "MULTI",      "WC-MILANO"),
    ("EMP-009", "Francesca Vitale",  "ELECTRICAL", "WC-TORINO"),
    ("EMP-010", "Roberto Costa",     "ELECTRICAL", "WC-TORINO"),
    ("EMP-011", "Valentina Gallo",   "MECHANICAL", "WC-TORINO"),
    ("EMP-012", "Matteo Ricci",      "MECHANICAL", "WC-TORINO"),
    ("EMP-013", "Claudia Bruno",     "MECHANICAL", "WC-TORINO"),
    ("EMP-014", "Stefano Lombardi",  "MULTI",      "WC-TORINO"),
    ("EMP-015", "Elena Moretti",     "MULTI",      "WC-TORINO"),
    ("EMP-016", "Antonio Fontana",   "ELECTRICAL", "WC-BERGAMO"),
    ("EMP-017", "Giulia Caruso",     "MECHANICAL", "WC-BERGAMO"),
    ("EMP-018", "Fabio Rizzo",       "MECHANICAL", "WC-BERGAMO"),
    ("EMP-019", "Cristina De Luca",  "MULTI",      "WC-BERGAMO"),
    ("EMP-020", "Davide Greco",      "MULTI",      "WC-BERGAMO"),
]
OP_IDS: dict[str, uuid.UUID] = {e[0]: sid(f"op:{e[0]}") for e in OP_DEFS}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _agg_domain(agg_code: str) -> str:
    if agg_code in ("AGG-006", "AGG-007", "AGG-008", "AGG-009"):
        return "ELETTRICO"
    if agg_code in ("AGG-010", "AGG-011", "AGG-012"):
        return "STRUTTURA"
    return "IDRAULICO"


def _op_types_for_domain(domain: str) -> list[str]:
    if domain == "ELETTRICO":
        return ["ELECTRICAL", "ELECTRICAL", "ELECTRICAL", "GENERAL"]
    if domain == "STRUTTURA":
        return ["MECHANICAL", "MECHANICAL", "MECHANICAL", "GENERAL"]
    return ["MECHANICAL", "MECHANICAL", "GENERAL"]


def _grp_domain(grp_code: str) -> str:
    parent = next(g[2] for g in GRP_DEFS if g[0] == grp_code)
    return _agg_domain(parent)


def _macro_wc(macro: str) -> uuid.UUID:
    return WC_IDS["WC-BERGAMO"] if macro == "MA-003" else WC_IDS["WC-MILANO"]


def _agg_wc(agg_code: str) -> uuid.UUID:
    domain = _agg_domain(agg_code)
    if domain == "STRUTTURA":
        return WC_IDS["WC-BERGAMO"]
    return WC_IDS["WC-MILANO"]


def _grp_wc(grp_code: str) -> uuid.UUID:
    parent_agg = next(g[2] for g in GRP_DEFS if g[0] == grp_code)
    return _agg_wc(parent_agg)


# ─── Seed functions ───────────────────────────────────────────────────────────

async def seed_workcenters(conn: asyncpg.Connection) -> None:
    data = [
        (WC_IDS["WC-MILANO"],  "WC-MILANO",  "Officina Milano",  "Milano",  "Officina principale", True),
        (WC_IDS["WC-TORINO"],  "WC-TORINO",  "Officina Torino",  "Torino",  "Officina Torino",     True),
        (WC_IDS["WC-BERGAMO"], "WC-BERGAMO", "Officina Bergamo", "Bergamo", "Officina Bergamo",    True),
    ]
    await conn.executemany(
        """
        INSERT INTO workcenters (id, code, name, location, description, is_active)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (code) DO NOTHING
        """,
        data,
    )


async def seed_machine_model(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO machine_models (id, code, name, description)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (code) DO NOTHING
        """,
        MM_TX500_ID, "TX500", "TURBOPRESS-X500", "Presse idraulica industriale serie X500",
    )


async def seed_shifts(conn: asyncpg.Connection) -> None:
    data = [
        (SH_IDS["Mattina"],    "Mattina",    time(6, 0),  time(14, 0), 30, True),
        (SH_IDS["Pomeriggio"], "Pomeriggio", time(14, 0), time(22, 0), 30, True),
        (SH_IDS["Notte"],      "Notte",      time(22, 0), time(6, 0),  30, True),
    ]
    await conn.executemany(
        """
        INSERT INTO shifts (id, name, start_time, end_time, break_duration_minutes, is_active)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (name) DO NOTHING
        """,
        data,
    )


async def seed_skill_workcenter_mapping(conn: asyncpg.Connection) -> None:
    rows = []
    for wc_code, wc_id in WC_IDS.items():
        for skill, can_el, can_mec, can_gen in [
            ("ELECTRICAL", True,  False, True),
            ("MECHANICAL", False, True,  True),
            ("MULTI",      True,  True,  True),
        ]:
            rows.append((sid(f"swm:{wc_code}:{skill}"), skill, wc_id, can_el, can_mec, can_gen))
    await conn.executemany(
        """
        INSERT INTO skill_workcenter_mapping
            (id, skill, workcenter_id, can_do_electrical, can_do_mechanical, can_do_general)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (skill, workcenter_id) DO NOTHING
        """,
        rows,
    )


async def seed_operators(conn: asyncpg.Connection) -> None:
    rows = [
        (OP_IDS[emp_id], emp_id, name, skill, WC_IDS[wc], True)
        for emp_id, name, skill, wc in OP_DEFS
    ]
    await conn.executemany(
        """
        INSERT INTO operators (id, employee_id, full_name, skill, workcenter_id, is_active)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (employee_id) DO NOTHING
        """,
        rows,
    )


async def seed_machine_order(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO machine_orders (id, sap_order_id, machine_model_id, description, status, workcenter_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (sap_order_id) DO NOTHING
        """,
        MACH_ORDER_ID, "ORD-MACH-001", MM_TX500_ID,
        "TURBOPRESS-X500 — Ordine cliente C-2024-001",
        "IN_PROGRESS", WC_IDS["WC-MILANO"], NOW,
    )


async def seed_bom(conn: asyncpg.Connection) -> None:
    """Insert production_orders for all BOM levels."""

    def po(
        po_id: uuid.UUID,
        sap_id: str,
        parent_id: uuid.UUID | None,
        parent_mat: str | None,
        level: str,
        mat_code: str,
        desc: str,
        wc_id: uuid.UUID | None,
    ) -> tuple:
        return (
            po_id, sap_id, parent_id, parent_mat, MACH_ORDER_ID,
            level, mat_code, desc, 1, "PZ", wc_id,
            0.0, "PLANNED", None, False, False, NOW,
        )

    rows: list[tuple] = []

    # Machine-level production order (wraps the machine order)
    rows.append(po(
        PROD_MACH_ID, "ORD-MACH-001", None, None,
        "MACHINE", "TX500-MACH", "TURBOPRESS-X500", WC_IDS["WC-MILANO"],
    ))

    # Macroaggregati
    MACRO_DEFS = [
        ("MA-001", "Gruppo Idraulico",   "MA-001-MAT", WC_IDS["WC-MILANO"]),
        ("MA-002", "Quadro Elettrico",   "MA-002-MAT", WC_IDS["WC-MILANO"]),
        ("MA-003", "Struttura Portante", "MA-003-MAT", WC_IDS["WC-BERGAMO"]),
    ]
    for code, desc, mat, wc in MACRO_DEFS:
        rows.append(po(
            MACRO_IDS[code], f"SAP-{code}", PROD_MACH_ID, "TX500-MACH",
            "MACROAGGREGATE", mat, desc, wc,
        ))

    # Aggregati
    AGG_DEFS = [
        ("AGG-001", "Cilindro Principale",   "MA-001"),
        ("AGG-002", "Pompa Olio",            "MA-001"),
        ("AGG-003", "Collettore",            "MA-001"),
        ("AGG-004", "Accumulatore",          "MA-001"),
        ("AGG-005", "Filtro Idraulico",      "MA-001"),
        ("AGG-006", "Armadio Principale",    "MA-002"),
        ("AGG-007", "Modulo PLC",            "MA-002"),
        ("AGG-008", "Pannello HMI",          "MA-002"),
        ("AGG-009", "Quadro Distribuzione",  "MA-002"),
        ("AGG-010", "Telaio Base",           "MA-003"),
        ("AGG-011", "Montanti",              "MA-003"),
        ("AGG-012", "Traversa",              "MA-003"),
    ]
    for code, desc, parent_macro in AGG_DEFS:
        rows.append(po(
            AGG_IDS[code], f"SAP-{code}",
            MACRO_IDS[parent_macro], f"{parent_macro}-MAT",
            "AGGREGATE", f"{code}-MAT", desc, _agg_wc(code),
        ))

    # Gruppi
    for grp_code, grp_desc, parent_agg in GRP_DEFS:
        rows.append(po(
            GRP_IDS[grp_code], f"SAP-{grp_code}",
            AGG_IDS[parent_agg], f"{parent_agg}-MAT",
            "GROUP", f"{grp_code}-MAT", grp_desc, _grp_wc(grp_code),
        ))

    # Componenti (3-6 per gruppo, con random.seed(42) già impostato in main)
    comp_counter = 1
    domain_prefix = {"IDRAULICO": "IDR", "ELETTRICO": "ELE", "STRUTTURA": "STR"}
    units = ["PZ", "MT", "KG", "M2"]
    for grp_code, grp_desc, parent_agg in GRP_DEFS:
        dom = _grp_domain(grp_code)
        pfx = domain_prefix[dom]
        n = random.randint(3, 6)
        for i in range(1, n + 1):
            mat = f"{pfx}-{comp_counter:04d}"
            sap = f"COMP-{grp_code}-{i:02d}"
            is_purchase = random.random() < 0.7
            qty = random.randint(1, 20)
            unit = random.choice(units)
            rows.append((
                sid(f"po:{sap}"), sap,
                GRP_IDS[grp_code], f"{grp_code}-MAT",
                MACH_ORDER_ID,
                "COMPONENT", mat,
                f"{mat} — {grp_desc}",
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
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17
        )
        ON CONFLICT (sap_order_id) DO NOTHING
        """,
        rows,
    )


async def seed_z_orders_link(conn: asyncpg.Connection) -> None:
    """Mirror the production_orders hierarchy in z_orders_link."""
    rows: list[tuple] = []

    def link(child_id: uuid.UUID, parent_id: uuid.UUID,
             parent_mat: str, child_mat: str, level: str) -> tuple:
        return (sid(f"zol:{child_id}:{parent_id}"),
                child_id, parent_id, parent_mat, child_mat, level, "BOM")

    # MACRO → MACHINE
    for code in ["MA-001", "MA-002", "MA-003"]:
        rows.append(link(MACRO_IDS[code], PROD_MACH_ID, "TX500-MACH", f"{code}-MAT", "MACROAGGREGATE"))

    # AGG → MACRO
    AGG_PARENT = {
        "AGG-001": "MA-001", "AGG-002": "MA-001", "AGG-003": "MA-001",
        "AGG-004": "MA-001", "AGG-005": "MA-001",
        "AGG-006": "MA-002", "AGG-007": "MA-002", "AGG-008": "MA-002", "AGG-009": "MA-002",
        "AGG-010": "MA-003", "AGG-011": "MA-003", "AGG-012": "MA-003",
    }
    for agg_code, parent_macro in AGG_PARENT.items():
        rows.append(link(
            AGG_IDS[agg_code], MACRO_IDS[parent_macro],
            f"{parent_macro}-MAT", f"{agg_code}-MAT", "AGGREGATE",
        ))

    # GRP → AGG
    for grp_code, _, parent_agg in GRP_DEFS:
        rows.append(link(
            GRP_IDS[grp_code], AGG_IDS[parent_agg],
            f"{parent_agg}-MAT", f"{grp_code}-MAT", "GROUP",
        ))

    await conn.executemany(
        """
        INSERT INTO z_orders_link
            (id, child_order_id, parent_order_id, parent_material, child_material, level, link_type)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (child_order_id, parent_order_id) DO NOTHING
        """,
        rows,
    )


async def seed_reference_points(conn: asyncpg.Connection) -> None:
    """Insert all reference points for model TX500.

    RP structure: each non-component order has RPs pointing ONLY to its
    direct BOM children. Groups have no RPs (children are components).

    target_level values: MACROAGGREGATE | AGGREGATE | GROUP
    """
    # (code, name, target_level, target_order_material)
    rp_defs: list[tuple[str, str, str, str]] = [
        # ── MACHINE level — 3 RPs → macroaggregates ──────────────────────────
        # Semantics: each RP represents readiness of one macroaggregate to be
        # integrated into the machine. DAG: MA-003 (structure) must come first.
        ("RP-M-01", "Completamento Struttura Portante",    "MACROAGGREGATE", "MA-003-MAT"),
        ("RP-M-02", "Completamento Gruppo Idraulico",      "MACROAGGREGATE", "MA-001-MAT"),
        ("RP-M-03", "Completamento Quadro Elettrico",      "MACROAGGREGATE", "MA-002-MAT"),

        # ── MA-001 level — 5 RPs → aggregates of MA-001 ──────────────────────
        # Hydraulic group: cylinder first, then pump, then collector + accumulator
        ("RP-MA1-01", "Completamento Cilindro Principale", "AGGREGATE", "AGG-001-MAT"),
        ("RP-MA1-02", "Completamento Pompa Olio",          "AGGREGATE", "AGG-002-MAT"),
        ("RP-MA1-03", "Completamento Collettore",          "AGGREGATE", "AGG-003-MAT"),
        ("RP-MA1-04", "Completamento Accumulatore",        "AGGREGATE", "AGG-004-MAT"),
        ("RP-MA1-05", "Completamento Filtro Idraulico",    "AGGREGATE", "AGG-005-MAT"),

        # ── MA-002 level — 4 RPs → aggregates of MA-002 ──────────────────────
        # Electrical panel: cabinet first, then PLC and HMI in parallel, then distribution
        ("RP-MA2-01", "Completamento Armadio Principale",  "AGGREGATE", "AGG-006-MAT"),
        ("RP-MA2-02", "Completamento Modulo PLC",          "AGGREGATE", "AGG-007-MAT"),
        ("RP-MA2-03", "Completamento Pannello HMI",        "AGGREGATE", "AGG-008-MAT"),
        ("RP-MA2-04", "Completamento Quadro Distribuzione","AGGREGATE", "AGG-009-MAT"),

        # ── MA-003 level — 3 RPs → aggregates of MA-003 ──────────────────────
        # Structure: base frame first, then uprights and crossbar in parallel
        ("RP-MA3-01", "Completamento Telaio Base",         "AGGREGATE", "AGG-010-MAT"),
        ("RP-MA3-02", "Completamento Montanti",            "AGGREGATE", "AGG-011-MAT"),
        ("RP-MA3-03", "Completamento Traversa",            "AGGREGATE", "AGG-012-MAT"),

        # ── AGG-001 level — 3 RPs → groups of AGG-001 ────────────────────────
        ("RP-A001-01", "Completamento Kit Guarnizioni Cilindro", "GROUP", "GRP-001-MAT"),
        ("RP-A001-02", "Completamento Gruppo Pistoni",           "GROUP", "GRP-002-MAT"),
        ("RP-A001-03", "Completamento Flangia Attacco",          "GROUP", "GRP-003-MAT"),

        # ── AGG-002 level — 4 RPs → groups of AGG-002 ────────────────────────
        ("RP-A002-01", "Completamento Corpo Pompa",         "GROUP", "GRP-004-MAT"),
        ("RP-A002-02", "Completamento Gruppo Ingranaggi",   "GROUP", "GRP-005-MAT"),
        ("RP-A002-03", "Completamento Coperchio Pompa",     "GROUP", "GRP-006-MAT"),
        ("RP-A002-04", "Completamento Kit Tenute Pompa",    "GROUP", "GRP-007-MAT"),

        # ── AGG-003 level — 3 RPs → groups of AGG-003 ────────────────────────
        ("RP-A003-01", "Completamento Blocco Valvole",      "GROUP", "GRP-008-MAT"),
        ("RP-A003-02", "Completamento Raccorderia",         "GROUP", "GRP-009-MAT"),
        ("RP-A003-03", "Completamento Supporti Collettore", "GROUP", "GRP-010-MAT"),

        # ── AGG-004 level — 4 RPs → groups of AGG-004 ────────────────────────
        ("RP-A004-01", "Completamento Serbatoio Accumulatore", "GROUP", "GRP-011-MAT"),
        ("RP-A004-02", "Completamento Membrana Separatrice",   "GROUP", "GRP-012-MAT"),
        ("RP-A004-03", "Completamento Valvola Gas N2",         "GROUP", "GRP-013-MAT"),
        ("RP-A004-04", "Completamento Staffa Fissaggio",       "GROUP", "GRP-014-MAT"),

        # ── AGG-005 level — 3 RPs → groups of AGG-005 ────────────────────────
        ("RP-A005-01", "Completamento Corpo Filtro",        "GROUP", "GRP-015-MAT"),
        ("RP-A005-02", "Completamento Elemento Filtrante",  "GROUP", "GRP-016-MAT"),
        ("RP-A005-03", "Completamento Bypass Filtro",       "GROUP", "GRP-017-MAT"),

        # ── AGG-006 level — 4 RPs → groups of AGG-006 ────────────────────────
        ("RP-A006-01", "Completamento Struttura Armadio",   "GROUP", "GRP-018-MAT"),
        ("RP-A006-02", "Completamento Pannello Porta",      "GROUP", "GRP-019-MAT"),
        ("RP-A006-03", "Completamento Canalina Cablaggio",  "GROUP", "GRP-020-MAT"),
        ("RP-A006-04", "Completamento Barra DIN Principale","GROUP", "GRP-021-MAT"),

        # ── AGG-007 level — 3 RPs → groups of AGG-007 ────────────────────────
        ("RP-A007-01", "Completamento CPU PLC",             "GROUP", "GRP-022-MAT"),
        ("RP-A007-02", "Completamento Moduli IO",           "GROUP", "GRP-023-MAT"),
        ("RP-A007-03", "Completamento Alimentatore PLC",    "GROUP", "GRP-024-MAT"),

        # ── AGG-008 level — 4 RPs → groups of AGG-008 ────────────────────────
        ("RP-A008-01", "Completamento Schermo Touch",       "GROUP", "GRP-025-MAT"),
        ("RP-A008-02", "Completamento Supporto HMI",        "GROUP", "GRP-026-MAT"),
        ("RP-A008-03", "Completamento Cablaggio HMI",       "GROUP", "GRP-027-MAT"),
        ("RP-A008-04", "Completamento PC Industriale",      "GROUP", "GRP-028-MAT"),

        # ── AGG-009 level — 3 RPs → groups of AGG-009 ────────────────────────
        ("RP-A009-01", "Completamento Interruttori Principali",   "GROUP", "GRP-029-MAT"),
        ("RP-A009-02", "Completamento Morsettiera Distribuzione", "GROUP", "GRP-030-MAT"),
        ("RP-A009-03", "Completamento Protezioni Motori",         "GROUP", "GRP-031-MAT"),

        # ── AGG-010 level — 3 RPs → groups of AGG-010 ────────────────────────
        ("RP-A010-01", "Completamento Longheroni Base",     "GROUP", "GRP-032-MAT"),
        ("RP-A010-02", "Completamento Traversi Inferiori",  "GROUP", "GRP-033-MAT"),
        ("RP-A010-03", "Completamento Piastre Ancoraggio",  "GROUP", "GRP-034-MAT"),

        # ── AGG-011 level — 4 RPs → groups of AGG-011 ────────────────────────
        ("RP-A011-01", "Completamento Colonne Verticali",   "GROUP", "GRP-035-MAT"),
        ("RP-A011-02", "Completamento Rinforzi Laterali",   "GROUP", "GRP-036-MAT"),
        ("RP-A011-03", "Completamento Giunti Colonne",      "GROUP", "GRP-037-MAT"),
        ("RP-A011-04", "Completamento Tappi Chiusura",      "GROUP", "GRP-038-MAT"),

        # ── AGG-012 level — 2 RPs → groups of AGG-012 ────────────────────────
        ("RP-A012-01", "Completamento Trave Superiore",     "GROUP", "GRP-039-MAT"),
        ("RP-A012-02", "Completamento Connettori Traversa", "GROUP", "GRP-040-MAT"),
    ]

    rows = [
        (RP_IDS[code], code, name, MM_TX500_ID, target_level, target_mat)
        for code, name, target_level, target_mat in rp_defs
    ]
    await conn.executemany(
        """
        INSERT INTO reference_points (id, code, name, machine_model_id, target_level, target_order_material)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (machine_model_id, code) DO NOTHING
        """,
        rows,
    )


async def seed_reference_point_precedences(conn: asyncpg.Connection) -> None:
    """Insert DAG edges between reference points.

    Rules:
    - Edges exist ONLY between RPs at the same parent-order level.
    - predecessor → successor: successor cannot start until predecessor's
      target order (and all its BOM children) is fully completed.
    - DAG is acyclic by construction (verified by topological ordering below).

    Topological level order per group:
      MACHINE:  RP-M-01 → RP-M-02, RP-M-03
      MA-001:   RP-MA1-01 → RP-MA1-02 → RP-MA1-03, RP-MA1-05
                          → RP-MA1-04
      MA-002:   RP-MA2-01 → RP-MA2-02, RP-MA2-03 → RP-MA2-04
      MA-003:   RP-MA3-01 → RP-MA3-02, RP-MA3-03
      AGGs:     linear or simple fan-out sequences
    """
    # (predecessor_code, successor_code)
    edges: list[tuple[str, str]] = [
        # ── MACHINE level ─────────────────────────────────────────────────────
        ("RP-M-01", "RP-M-02"),   # Struttura Portante → Gruppo Idraulico
        ("RP-M-01", "RP-M-03"),   # Struttura Portante → Quadro Elettrico
        # RP-M-02 ‖ RP-M-03 (parallel, no edge between them)

        # ── MA-001 level ───────────────────────────────────────────────────────
        ("RP-MA1-01", "RP-MA1-02"),   # Cilindro → Pompa Olio
        ("RP-MA1-01", "RP-MA1-04"),   # Cilindro → Accumulatore (parallel to pump)
        ("RP-MA1-02", "RP-MA1-03"),   # Pompa Olio → Collettore
        ("RP-MA1-01", "RP-MA1-03"),   # Cilindro → Collettore (both prereqs)
        ("RP-MA1-02", "RP-MA1-05"),   # Pompa Olio → Filtro Idraulico

        # ── MA-002 level ───────────────────────────────────────────────────────
        ("RP-MA2-01", "RP-MA2-02"),   # Armadio → Modulo PLC
        ("RP-MA2-01", "RP-MA2-03"),   # Armadio → Pannello HMI
        ("RP-MA2-02", "RP-MA2-04"),   # Modulo PLC → Quadro Distribuzione
        ("RP-MA2-03", "RP-MA2-04"),   # Pannello HMI → Quadro Distribuzione

        # ── MA-003 level ───────────────────────────────────────────────────────
        ("RP-MA3-01", "RP-MA3-02"),   # Telaio Base → Montanti
        ("RP-MA3-01", "RP-MA3-03"),   # Telaio Base → Traversa

        # ── AGG-001 level ──────────────────────────────────────────────────────
        ("RP-A001-01", "RP-A001-02"),
        ("RP-A001-02", "RP-A001-03"),

        # ── AGG-002 level ──────────────────────────────────────────────────────
        ("RP-A002-01", "RP-A002-02"),
        ("RP-A002-02", "RP-A002-03"),
        ("RP-A002-01", "RP-A002-04"),   # Kit Tenute in parallelo con catena principale

        # ── AGG-003 level ──────────────────────────────────────────────────────
        ("RP-A003-01", "RP-A003-02"),
        ("RP-A003-02", "RP-A003-03"),

        # ── AGG-004 level ──────────────────────────────────────────────────────
        ("RP-A004-01", "RP-A004-02"),
        ("RP-A004-01", "RP-A004-03"),
        ("RP-A004-02", "RP-A004-04"),
        ("RP-A004-03", "RP-A004-04"),

        # ── AGG-005 level ──────────────────────────────────────────────────────
        ("RP-A005-01", "RP-A005-02"),
        ("RP-A005-02", "RP-A005-03"),

        # ── AGG-006 level ──────────────────────────────────────────────────────
        ("RP-A006-01", "RP-A006-02"),
        ("RP-A006-01", "RP-A006-03"),
        ("RP-A006-01", "RP-A006-04"),

        # ── AGG-007 level ──────────────────────────────────────────────────────
        ("RP-A007-01", "RP-A007-02"),
        ("RP-A007-01", "RP-A007-03"),

        # ── AGG-008 level ──────────────────────────────────────────────────────
        ("RP-A008-01", "RP-A008-02"),
        ("RP-A008-02", "RP-A008-03"),
        ("RP-A008-04", "RP-A008-03"),   # PC Industriale → Cablaggio HMI

        # ── AGG-009 level ──────────────────────────────────────────────────────
        ("RP-A009-01", "RP-A009-02"),
        ("RP-A009-02", "RP-A009-03"),

        # ── AGG-010 level ──────────────────────────────────────────────────────
        ("RP-A010-01", "RP-A010-02"),
        ("RP-A010-02", "RP-A010-03"),

        # ── AGG-011 level ──────────────────────────────────────────────────────
        ("RP-A011-01", "RP-A011-02"),
        ("RP-A011-01", "RP-A011-03"),
        ("RP-A011-02", "RP-A011-04"),
        ("RP-A011-03", "RP-A011-04"),

        # ── AGG-012 level ──────────────────────────────────────────────────────
        ("RP-A012-01", "RP-A012-02"),
    ]

    rows = [
        (
            sid(f"rpp:{pred}:{succ}"),
            RP_IDS[succ],    # reference_point_id (il successore che viene bloccato)
            RP_IDS[pred],    # predecessor_reference_point_id
            MM_TX500_ID,
        )
        for pred, succ in edges
    ]
    await conn.executemany(
        """
        INSERT INTO reference_point_precedences
            (id, reference_point_id, predecessor_reference_point_id, machine_model_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT DO NOTHING
        """,
        rows,
    )


async def seed_routings_and_operations(conn: asyncpg.Connection) -> None:
    """Create one routing per non-component order, plus N operations each.

    RP assignment (v2):
    - MACHINE order ops  → RP-M-01..03  (one op per macroaggregate, 3 total)
    - MA-001 ops         → RP-MA1-01..05 (one op per AGG child, 5 total)
    - MA-002 ops         → RP-MA2-01..04 (4 total)
    - MA-003 ops         → RP-MA3-01..03 (3 total)
    - AGG-001 ops        → RP-A001-01..03 (one op per GRP child)
    - ... (all aggregates similarly)
    - GROUP ops          → no RP (children are purchase components)
    """
    routing_rows: list[tuple] = []
    operation_rows: list[tuple] = []

    def add_routing_ops(
        po_id: uuid.UUID,
        po_sap: str,
        domain: str,
        wc_id: uuid.UUID,
        rp_sequence: list[uuid.UUID] | None = None,
    ) -> None:
        """Add routing + operations.

        If rp_sequence is provided, creates exactly len(rp_sequence) operations,
        each mapped 1:1 to a RP (op seq 1 → rp_sequence[0], etc.).
        Otherwise creates random.randint(3,6) ops without RPs.
        """
        r_id = sid(f"rt:{po_sap}")
        routing_rows.append((r_id, po_id, f"ROUT-{po_sap}", "SIMULTANEOUS"))
        op_types = _op_types_for_domain(domain)
        n_ops = len(rp_sequence) if rp_sequence else random.randint(3, 6)
        for seq in range(1, n_ops + 1):
            op_type = random.choice(op_types)
            duration = random.randint(120, 480)
            rp_id = rp_sequence[seq - 1] if rp_sequence else None
            op_id = sid(f"op_inst:{po_sap}:{seq}")
            operation_rows.append((
                op_id, r_id, f"OP-{po_sap}-{seq:02d}", seq,
                f"Operazione {seq} — {po_sap}", op_type,
                wc_id, duration, None, 0.0, "PENDING",
                rp_id, True,
            ))

    # ── MACHINE order — 3 operations (one per macroaggregate RP) ─────────────
    r_mach_id = sid("rt:ORD-MACH-001")
    routing_rows.append((r_mach_id, PROD_MACH_ID, "ROUT-ORD-MACH-001", "SIMULTANEOUS"))
    _mach_op_info = [
        # (description, op_type, rp_id)
        ("Collaudo integrazione Struttura Portante", "MECHANICAL", RP_IDS["RP-M-01"]),
        ("Collaudo integrazione Gruppo Idraulico",   "MECHANICAL", RP_IDS["RP-M-02"]),
        ("Collaudo integrazione Quadro Elettrico",   "ELECTRICAL", RP_IDS["RP-M-03"]),
    ]
    for seq, (desc, op_type, rp_id) in enumerate(_mach_op_info, start=1):
        operation_rows.append((
            sid(f"op_inst:ORD-MACH-001:{seq}"),
            r_mach_id, f"OP-MACH-{seq:02d}", seq,
            desc, op_type, WC_IDS["WC-MILANO"],
            random.randint(120, 480), None, 0.0, "PENDING",
            rp_id, True,
        ))

    # ── MACROAGGREGATI — ops con RP verso i loro aggregati figli ─────────────
    macro_rp_seqs: dict[str, list[uuid.UUID]] = {
        "MA-001": [
            RP_IDS["RP-MA1-01"],  # → AGG-001 Cilindro Principale
            RP_IDS["RP-MA1-02"],  # → AGG-002 Pompa Olio
            RP_IDS["RP-MA1-03"],  # → AGG-003 Collettore
            RP_IDS["RP-MA1-04"],  # → AGG-004 Accumulatore
            RP_IDS["RP-MA1-05"],  # → AGG-005 Filtro Idraulico
        ],
        "MA-002": [
            RP_IDS["RP-MA2-01"],  # → AGG-006 Armadio Principale
            RP_IDS["RP-MA2-02"],  # → AGG-007 Modulo PLC
            RP_IDS["RP-MA2-03"],  # → AGG-008 Pannello HMI
            RP_IDS["RP-MA2-04"],  # → AGG-009 Quadro Distribuzione
        ],
        "MA-003": [
            RP_IDS["RP-MA3-01"],  # → AGG-010 Telaio Base
            RP_IDS["RP-MA3-02"],  # → AGG-011 Montanti
            RP_IDS["RP-MA3-03"],  # → AGG-012 Traversa
        ],
    }
    macro_info = [
        ("MA-001", "IDRAULICO", WC_IDS["WC-MILANO"]),
        ("MA-002", "ELETTRICO", WC_IDS["WC-MILANO"]),
        ("MA-003", "STRUTTURA", WC_IDS["WC-BERGAMO"]),
    ]
    for code, dom, wc in macro_info:
        add_routing_ops(MACRO_IDS[code], f"SAP-{code}", dom, wc,
                        rp_sequence=macro_rp_seqs[code])

    # ── AGGREGATI — ops con RP verso i loro gruppi figli ─────────────────────
    agg_rp_seqs: dict[str, list[uuid.UUID]] = {
        "AGG-001": [RP_IDS["RP-A001-01"], RP_IDS["RP-A001-02"], RP_IDS["RP-A001-03"]],
        "AGG-002": [RP_IDS["RP-A002-01"], RP_IDS["RP-A002-02"], RP_IDS["RP-A002-03"], RP_IDS["RP-A002-04"]],
        "AGG-003": [RP_IDS["RP-A003-01"], RP_IDS["RP-A003-02"], RP_IDS["RP-A003-03"]],
        "AGG-004": [RP_IDS["RP-A004-01"], RP_IDS["RP-A004-02"], RP_IDS["RP-A004-03"], RP_IDS["RP-A004-04"]],
        "AGG-005": [RP_IDS["RP-A005-01"], RP_IDS["RP-A005-02"], RP_IDS["RP-A005-03"]],
        "AGG-006": [RP_IDS["RP-A006-01"], RP_IDS["RP-A006-02"], RP_IDS["RP-A006-03"], RP_IDS["RP-A006-04"]],
        "AGG-007": [RP_IDS["RP-A007-01"], RP_IDS["RP-A007-02"], RP_IDS["RP-A007-03"]],
        "AGG-008": [RP_IDS["RP-A008-01"], RP_IDS["RP-A008-02"], RP_IDS["RP-A008-03"], RP_IDS["RP-A008-04"]],
        "AGG-009": [RP_IDS["RP-A009-01"], RP_IDS["RP-A009-02"], RP_IDS["RP-A009-03"]],
        "AGG-010": [RP_IDS["RP-A010-01"], RP_IDS["RP-A010-02"], RP_IDS["RP-A010-03"]],
        "AGG-011": [RP_IDS["RP-A011-01"], RP_IDS["RP-A011-02"], RP_IDS["RP-A011-03"], RP_IDS["RP-A011-04"]],
        "AGG-012": [RP_IDS["RP-A012-01"], RP_IDS["RP-A012-02"]],
    }
    for agg_code in AGG_CODES:
        dom = _agg_domain(agg_code)
        wc = _agg_wc(agg_code)
        add_routing_ops(AGG_IDS[agg_code], f"SAP-{agg_code}", dom, wc,
                        rp_sequence=agg_rp_seqs[agg_code])

    # ── GRUPPI — ops senza RP (figli sono componenti senza routing) ───────────
    for grp_code, _, _ in GRP_DEFS:
        dom = _grp_domain(grp_code)
        wc = _grp_wc(grp_code)
        add_routing_ops(GRP_IDS[grp_code], f"SAP-{grp_code}", dom, wc)

    await conn.executemany(
        """
        INSERT INTO routings (id, production_order_id, sap_routing_id, execution_mode)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (production_order_id) DO NOTHING
        """,
        routing_rows,
    )
    await conn.executemany(
        """
        INSERT INTO operations (
            id, routing_id, sap_operation_id, sequence_number, description,
            operation_type, workcenter_id,
            planned_duration_minutes, actual_duration_minutes,
            progress_pct, status, reference_point_id, can_be_interrupted
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (routing_id, sequence_number) DO NOTHING
        """,
        operation_rows,
    )


async def seed_missing_components(conn: asyncpg.Connection) -> None:
    """Insert 5 pre-defined missing components."""
    # GRP-001 child components
    grp001_comp = sid("po:COMP-GRP-001-01")
    rows = [
        (
            sid("mc:VLV-2200"), grp001_comp, "VLV-2200",
            "Valvola idraulica principale", TODAY + timedelta(days=7),
            False, None, True, "Pre-impostato dal seed",
        ),
        (
            sid("mc:CAB-450"), sid("po:COMP-GRP-018-01"), "CAB-450",
            "Cavo elettrico 25mm²", TODAY + timedelta(days=3),
            False, None, True, "Pre-impostato dal seed",
        ),
        (
            sid("mc:SEN-P100"), sid("po:COMP-GRP-004-01"), "SEN-P100",
            "Sensore pressione idraulica", TODAY + timedelta(days=12),
            False, None, True, "Pre-impostato dal seed",
        ),
        (
            sid("mc:VIT-M16"), sid("po:COMP-GRP-032-01"), "VIT-M16",
            "Vite speciale M16x80", TODAY + timedelta(days=1),
            False, None, True, "Pre-impostato dal seed",
        ),
        (
            sid("mc:GUA-200"), sid("po:COMP-GRP-001-02"), "GUA-200",
            "Guarnizione gomma 200mm", TODAY + timedelta(days=5),
            False, None, True, "Pre-impostato dal seed",
        ),
    ]
    await conn.executemany(
        """
        INSERT INTO missing_components
            (id, production_order_id, component_material, description,
             expected_arrival_date, is_arrived, arrival_confirmed_date,
             manually_flagged, notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT DO NOTHING
        """,
        rows,
    )


async def seed_operator_calendar(conn: asyncpg.Connection) -> None:
    """Generate 28-day calendar for all operators with ~8 random absences."""
    shifts_cycle = ["Mattina", "Pomeriggio", "Notte"]
    rows: list[tuple] = []
    absences: set[tuple[str, date]] = set()

    # Generate ~8 absences spread across operators and days
    op_ids_list = [e[0] for e in OP_DEFS]
    for _ in range(8):
        emp = random.choice(op_ids_list)
        day_offset = random.randint(0, 27)
        absences.add((emp, TODAY + timedelta(days=day_offset)))

    for emp_id, _, _, _ in OP_DEFS:
        for day_offset in range(28):
            cal_date = TODAY + timedelta(days=day_offset)
            is_absent = (emp_id, cal_date) in absences
            shift_name = shifts_cycle[day_offset % 3]
            rows.append((
                sid(f"cal:{emp_id}:{cal_date}"),
                OP_IDS[emp_id],
                cal_date,
                None if is_absent else SH_IDS[shift_name],
                not is_absent,
                "Assenza programmata" if is_absent else None,
                None,
            ))

    await conn.executemany(
        """
        INSERT INTO operator_calendar
            (id, operator_id, date, shift_id, is_available, notes, override_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (operator_id, date) DO NOTHING
        """,
        rows,
    )


async def seed_scenarios(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO schedule_scenarios
            (id, machine_order_id, name, objective_mode, is_active, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        SCENARIO_ID, MACH_ORDER_ID, "Scenario Base", "FINISH_BY_DATE", True, NOW,
    )


async def print_counts(conn: asyncpg.Connection) -> None:
    tables = [
        "workcenters", "machine_models", "shifts", "skill_workcenter_mapping",
        "operators", "machine_orders", "production_orders", "z_orders_link",
        "reference_points", "reference_point_precedences",
        "routings", "operations", "missing_components",
        "operator_calendar", "schedule_scenarios",
    ]
    print("\n────────────────────────────────────────────────────────────")
    print("  SEED COMPLETATO — conteggio righe per tabella")
    print("────────────────────────────────────────────────────────────")
    for t in tables:
        try:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:<40} {n:>5} righe")
        except Exception as exc:
            print(f"  {t:<40} ERRORE: {exc}")
    print("────────────────────────────────────────────────────────────\n")

    # Verifica rapida della coerenza RP
    rp_count = await conn.fetchval("SELECT COUNT(*) FROM reference_points")
    rpp_count = await conn.fetchval("SELECT COUNT(*) FROM reference_point_precedences")
    ops_with_rp = await conn.fetchval(
        "SELECT COUNT(*) FROM operations WHERE reference_point_id IS NOT NULL"
    )
    print(f"  [CHECK] {rp_count} reference points, {rpp_count} archi DAG")
    print(f"  [CHECK] {ops_with_rp} operazioni con reference_point_id assegnato")
    print(f"  [CHECK] Atteso: 57 RP, 43 archi, ops = somma di tutti i rp_sequence\n")


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main() -> None:
    random.seed(42)  # canonical position — set once, never changed
    conn: asyncpg.Connection = await asyncpg.connect(_DATABASE_URL)
    try:
        print("Connessione al database OK — avvio seed TURBOPRESS-X500 v2...")
        await seed_workcenters(conn)
        await seed_machine_model(conn)
        await seed_shifts(conn)
        await seed_skill_workcenter_mapping(conn)
        await seed_operators(conn)
        await seed_machine_order(conn)
        await seed_bom(conn)
        await seed_z_orders_link(conn)
        await seed_reference_points(conn)            # PRIMA dei routings
        await seed_reference_point_precedences(conn)
        await seed_routings_and_operations(conn)     # DOPO i reference points
        await seed_missing_components(conn)
        await seed_operator_calendar(conn)
        await seed_scenarios(conn)
        await print_counts(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())