"""
Seed script — TURBOPRESS-X500 complete mock data.
Usage:  cd backend && python -m app.db.seed
Idempotent: every INSERT uses ON CONFLICT DO NOTHING on a business-key.
random.seed(42) is set once at the top of main() and never changed.
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

RP_IDS: dict[str, uuid.UUID] = {f"RP-{i:03d}": sid(f"rp:RP-{i:03d}") for i in range(1, 11)}

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
    """Return 'ELETTRICO', 'IDRAULICO', or 'STRUTTURA' for an aggregate code."""
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
        (WC_IDS["WC-MILANO"],  "WC-MILANO",  "Officina Milano",  "Milano",  "Officina principale Milano", True),
        (WC_IDS["WC-TORINO"],  "WC-TORINO",  "Officina Torino",  "Torino",  "Officina Torino",            True),
        (WC_IDS["WC-BERGAMO"], "WC-BERGAMO", "Officina Bergamo", "Bergamo", "Officina Bergamo",           True),
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
        MM_TX500_ID, "TX500", "TURBOPRESS-X500",
        "Pressa industriale ad alta pressione per laminazione avanzata",
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
    rows: list[tuple] = []
    for skill, e_flag, m_flag, g_flag in [
        ("ELECTRICAL", True,  False, False),
        ("MECHANICAL", False, True,  False),
        ("MULTI",      True,  True,  True),
    ]:
        for wc_code, wc_id in WC_IDS.items():
            rows.append((
                sid(f"swm:{skill}:{wc_code}"),
                skill, wc_id, e_flag, m_flag, g_flag,
            ))
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
        INSERT INTO machine_orders
            (id, sap_order_id, machine_model_id, description, status, workcenter_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (sap_order_id) DO NOTHING
        """,
        MACH_ORDER_ID, "ORD-MACH-001", MM_TX500_ID,
        "Montaggio TURBOPRESS-X500 — commessa 2026/001",
        "PLANNED", WC_IDS["WC-MILANO"], NOW,
    )


async def seed_bom(conn: asyncpg.Connection) -> None:
    """Insert all production_orders: MACHINE → MACRO → AGG → GROUP → COMPONENT."""
    rows: list[tuple] = []

    def po(
        po_id: uuid.UUID,
        sap_order_id: str,
        parent_id: uuid.UUID | None,
        parent_material: str | None,
        level: str,
        material_code: str,
        description: str,
        workcenter_id: uuid.UUID | None,
        is_purchase: bool = False,
        is_untracked: bool = False,
    ) -> tuple:
        return (
            po_id, sap_order_id, parent_id, parent_material, MACH_ORDER_ID,
            level, material_code, description,
            1, "PZ", workcenter_id,
            0.0, "PLANNED",
            None, is_purchase, is_untracked, NOW,
        )

    # MACHINE level
    rows.append(po(
        PROD_MACH_ID, "ORD-MACH-001", None, None,
        "MACHINE", "TX500-MACH", "TURBOPRESS-X500 — Ordine Macchina",
        WC_IDS["WC-MILANO"],
    ))

    # Macroaggregati
    MACRO_DEFS = [
        ("MA-001", "MA-001-MAT", "Gruppo Idraulico",   "WC-MILANO"),
        ("MA-002", "MA-002-MAT", "Quadro Elettrico",   "WC-MILANO"),
        ("MA-003", "MA-003-MAT", "Struttura Portante", "WC-BERGAMO"),
    ]
    for code, mat, desc, wc in MACRO_DEFS:
        rows.append(po(
            MACRO_IDS[code], f"SAP-{code}", PROD_MACH_ID, "TX500-MACH",
            "MACROAGGREGATE", mat, desc, WC_IDS[wc],
        ))

    # Aggregati
    AGG_DEFS = [
        ("AGG-001", "Cilindro Principale",  "MA-001"),
        ("AGG-002", "Pompa Olio",           "MA-001"),
        ("AGG-003", "Collettore",           "MA-001"),
        ("AGG-004", "Accumulatore",         "MA-001"),
        ("AGG-005", "Filtro Idraulico",     "MA-001"),
        ("AGG-006", "Armadio Principale",   "MA-002"),
        ("AGG-007", "Modulo PLC",           "MA-002"),
        ("AGG-008", "Pannello HMI",         "MA-002"),
        ("AGG-009", "Quadro Distribuzione", "MA-002"),
        ("AGG-010", "Telaio Base",          "MA-003"),
        ("AGG-011", "Montanti",             "MA-003"),
        ("AGG-012", "Traversa",             "MA-003"),
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

    # Componenti (~150, 3-6 per gruppo)
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
    for code, mat, _, _ in [
        ("MA-001", "MA-001-MAT", None, None),
        ("MA-002", "MA-002-MAT", None, None),
        ("MA-003", "MA-003-MAT", None, None),
    ]:
        rows.append(link(MACRO_IDS[code], PROD_MACH_ID, "TX500-MACH", mat, "MACROAGGREGATE"))

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


async def seed_routings_and_operations(conn: asyncpg.Connection) -> None:
    """Create one routing per non-component order, plus 3-6 operations each."""

    routing_rows: list[tuple] = []
    operation_rows: list[tuple] = []

    def add_routing_ops(
        po_id: uuid.UUID,
        po_sap: str,
        domain: str,
        wc_id: uuid.UUID,
        rp_map: dict[int, uuid.UUID] | None = None,  # seq → rp_id for machine ops
    ) -> None:
        r_id = sid(f"rt:{po_sap}")
        routing_rows.append((r_id, po_id, f"ROUT-{po_sap}", "SIMULTANEOUS"))
        op_types = _op_types_for_domain(domain)
        n_ops = random.randint(3, 6)
        for seq in range(1, n_ops + 1):
            op_type = random.choice(op_types)
            duration = random.randint(120, 480)
            rp_id = (rp_map or {}).get(seq)
            op_id = sid(f"op_inst:{po_sap}:{seq}")
            operation_rows.append((
                op_id, r_id, f"OP-{po_sap}-{seq:02d}", seq,
                f"Operazione {seq} — {po_sap}", op_type,
                wc_id, duration, None, 0.0, "PENDING",
                rp_id, True,
            ))

    # Machine order — 10 fixed operations, one per reference point
    mach_rp_map = {i: RP_IDS[f"RP-{i:03d}"] for i in range(1, 11)}
    r_mach_id = sid("rt:ORD-MACH-001")
    routing_rows.append((r_mach_id, PROD_MACH_ID, "ROUT-ORD-MACH-001", "SIMULTANEOUS"))
    _mach_op_types = [
        "MECHANICAL", "MECHANICAL", "ELECTRICAL", "MECHANICAL",
        "MECHANICAL", "ELECTRICAL", "ELECTRICAL", "MECHANICAL",
        "MECHANICAL", "MECHANICAL",
    ]
    _mach_op_descs = [
        "Collaudo Struttura Portante",
        "Collaudo Gruppo Idraulico",
        "Collaudo Quadro Elettrico",
        "Collaudo Cilindro Principale",
        "Collaudo Pompa Olio",
        "Collaudo Armadio Principale",
        "Collaudo Modulo PLC",
        "Collaudo Telaio Base",
        "Collaudo Montanti",
        "Collaudo Collettore",
    ]
    for seq in range(1, 11):
        operation_rows.append((
            sid(f"op_inst:ORD-MACH-001:{seq}"),
            r_mach_id,
            f"OP-MACH-{seq:02d}",
            seq,
            _mach_op_descs[seq - 1],
            _mach_op_types[seq - 1],
            WC_IDS["WC-MILANO"],
            random.randint(120, 480),
            None, 0.0, "PENDING",
            RP_IDS[f"RP-{seq:03d}"],
            True,
        ))

    # Macroaggregati
    macro_info = [
        ("MA-001", "IDRAULICO", WC_IDS["WC-MILANO"]),
        ("MA-002", "ELETTRICO", WC_IDS["WC-MILANO"]),
        ("MA-003", "STRUTTURA", WC_IDS["WC-BERGAMO"]),
    ]
    for code, dom, wc in macro_info:
        add_routing_ops(MACRO_IDS[code], f"SAP-{code}", dom, wc)

    # Aggregati
    for agg_code in AGG_CODES:
        dom = _agg_domain(agg_code)
        wc = _agg_wc(agg_code)
        add_routing_ops(AGG_IDS[agg_code], f"SAP-{agg_code}", dom, wc)

    # Gruppi
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


async def seed_reference_points(conn: asyncpg.Connection) -> None:
    # (code, name, target_level, target_order_material)
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
        """
        INSERT INTO reference_points
            (id, code, name, machine_model_id, target_level, target_order_material)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (code, machine_model_id) DO NOTHING
        """,
        rows,
    )


async def seed_reference_point_precedences(conn: asyncpg.Connection) -> None:
    """
    DAG (acyclic — verified manually):
    RP-001 (root)
      ├─ RP-002, RP-003, RP-008
    RP-002 → RP-004
    RP-004 → RP-005, RP-010
    RP-003 → RP-006
    RP-006 → RP-007
    RP-008 → RP-009
    RP-005 → RP-010  (together with RP-004)
    """
    # (rp_code, predecessor_rp_code)
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
        (
            sid(f"rpp:{rp}:{pred}"),
            RP_IDS[rp], RP_IDS[pred], MM_TX500_ID,
        )
        for rp, pred in pairs
    ]
    await conn.executemany(
        """
        INSERT INTO reference_point_precedences
            (id, reference_point_id, predecessor_reference_point_id, machine_model_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (reference_point_id, predecessor_reference_point_id) DO NOTHING
        """,
        rows,
    )


async def seed_missing_components(conn: asyncpg.Connection) -> None:
    """5 pre-set missing components that block specific groups."""
    rows = [
        # (id, production_order_id, component_material, description,
        #  expected_arrival_date, is_arrived, manually_flagged, notes)
        (
            sid("mc:VLV-2200"),
            GRP_IDS["GRP-001"], "VLV-2200",
            "Valvola idraulica principale",
            TODAY + timedelta(days=7), False, False,
            "In attesa da fornitore Idro SpA",
        ),
        (
            sid("mc:CAB-450"),
            GRP_IDS["GRP-020"], "CAB-450",
            "Cavo elettrico 25mm²",
            TODAY + timedelta(days=3), False, False,
            "Spedizione in corso",
        ),
        (
            sid("mc:SEN-P100"),
            GRP_IDS["GRP-008"], "SEN-P100",
            "Sensore pressione idraulica",
            TODAY + timedelta(days=12), False, True,
            "Produzione custom — lead time esteso",
        ),
        (
            sid("mc:VIT-M16"),
            GRP_IDS["GRP-032"], "VIT-M16",
            "Vite speciale M16x80 DIN 933",
            TODAY + timedelta(days=1), False, False,
            "Attesa conferma quantità",
        ),
        (
            sid("mc:GUA-200"),
            GRP_IDS["GRP-015"], "GUA-200",
            "Guarnizione gomma EPDM 200mm",
            TODAY + timedelta(days=5), False, False,
            "Ordine confermato",
        ),
    ]
    await conn.executemany(
        """
        INSERT INTO missing_components
            (id, production_order_id, component_material, description,
             expected_arrival_date, is_arrived, manually_flagged, notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (production_order_id, component_material) DO NOTHING
        """,
        rows,
    )


async def seed_operator_calendar(conn: asyncpg.Connection) -> None:
    """28 days (4 weeks) from today for all 20 operators. ~8 random absences."""
    dates = [TODAY + timedelta(days=i) for i in range(28)]
    shifts_cycle = [SH_IDS["Mattina"], SH_IDS["Pomeriggio"], SH_IDS["Notte"]]

    # Pick 8 unique (employee_id, date) pairs as absences
    all_pairs = [(op_def[0], d) for op_def in OP_DEFS for d in dates]
    absence_set: set[tuple[str, date]] = set(random.sample(all_pairs, 8))

    rows: list[tuple] = []
    for op_idx, op_def in enumerate(OP_DEFS):
        emp_id = op_def[0]
        op_id = OP_IDS[emp_id]
        for day_idx, d in enumerate(dates):
            is_absent = (emp_id, d) in absence_set
            if is_absent:
                shift_id = None
                is_available = False
                notes: str | None = "Assenza programmata"
                override = "ABSENCE"
            else:
                shift_id = shifts_cycle[(op_idx + day_idx) % 3]
                is_available = True
                notes = None
                override = None
            rows.append((
                sid(f"cal:{emp_id}:{d.isoformat()}"),
                op_id, d, shift_id, is_available, notes, override,
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
    target = TODAY + timedelta(days=90)
    await conn.execute(
        """
        INSERT INTO schedule_scenarios
            (id, name, description, machine_order_id, objective_mode,
             target_finish_date, resource_set_json,
             created_at, is_active, is_baseline)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (name, machine_order_id) DO NOTHING
        """,
        SCENARIO_ID,
        "Scenario Base",
        "Scenario di scheduling di default — obiettivo FINISH_BY_DATE",
        MACH_ORDER_ID,
        "FINISH_BY_DATE",
        target,
        "{}",
        NOW,
        True,
        True,
    )


# ─── Count printer ────────────────────────────────────────────────────────────

async def print_counts(conn: asyncpg.Connection) -> None:
    tables = [
        "workcenters", "machine_models", "shifts", "skill_workcenter_mapping",
        "operators", "machine_orders", "production_orders", "z_orders_link",
        "routings", "operations", "reference_points", "reference_point_precedences",
        "missing_components", "operator_calendar", "schedule_scenarios",
    ]
    print("\n== Seed completato ==============================================")
    for t in tables:
        try:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:<35} {n:>5} righe")
        except Exception as exc:
            print(f"  {t:<35} ERRORE: {exc}")
    print("────────────────────────────────────────────────────────────\n")


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main() -> None:
    random.seed(42)  # canonical position — set once, never changed
    conn: asyncpg.Connection = await asyncpg.connect(_DATABASE_URL)
    try:
        print("Connessione al database OK — avvio seed TURBOPRESS-X500...")
        await seed_workcenters(conn)
        await seed_machine_model(conn)
        await seed_shifts(conn)
        await seed_skill_workcenter_mapping(conn)
        await seed_operators(conn)
        await seed_machine_order(conn)
        await seed_bom(conn)
        await seed_z_orders_link(conn)
        await seed_reference_points(conn)           # prima dei routings — le operazioni referenziano gli RP
        await seed_reference_point_precedences(conn)
        await seed_routings_and_operations(conn)    # dopo i reference points
        await seed_missing_components(conn)
        await seed_operator_calendar(conn)
        await seed_scenarios(conn)
        await print_counts(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
