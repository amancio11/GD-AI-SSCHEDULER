"""Export endpoints — CSV (UTF-8 BOM), JSON-SAP e PDF via reportlab.

Tre formati di export per il piano di scheduling:
- CSV: compatibile Excel italiano (BOM UTF-8, separatore ;)
- JSON-SAP: struttura pronta per essere importata in SAP DM
- PDF: report impaginato con KPI, schedule per operatore, mancanti e ritardi
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.delay import DelayEvent
from app.models.missing import MissingComponent
from app.models.operator import Operator
from app.models.routing import Operation
from app.models.schedule import ScheduleEntry, ScheduleScenario
from app.models.workcenter import Workcenter

router = APIRouter(prefix="/export", tags=["export"])


# ── helpers ───────────────────────────────────────────────────────────────────

async def _load_entries(scenario_id: uuid.UUID, db: AsyncSession) -> list[ScheduleEntry]:
    result = await db.execute(
        select(ScheduleEntry).where(ScheduleEntry.scenario_id == scenario_id)
    )
    return result.scalars().all()


async def _get_scenario(scenario_id: uuid.UUID, db: AsyncSession) -> ScheduleScenario:
    scenario = await db.get(ScheduleScenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


# ── CSV export ────────────────────────────────────────────────────────────────

@router.get("/scenario/{scenario_id}/csv")
async def export_csv(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Export schedule as UTF-8 BOM CSV (Excel-compatible for Italian locale)."""
    scenario = await _get_scenario(scenario_id, db)
    entries  = await _load_entries(scenario_id, db)

    def generate() -> AsyncIterator[bytes]:
        buf = io.StringIO()
        # UTF-8 BOM for Excel compatibility
        buf.write("\ufeff")
        writer = csv.DictWriter(buf, fieldnames=[
            "scenario_name", "sap_order_id", "order_description",
            "sap_operation_id", "operation_description", "operation_type",
            "operator_name", "operator_skill", "workcenter",
            "scheduled_start", "scheduled_end", "duration_hours",
            "status", "delay_minutes",
        ])
        writer.writeheader()

        for e in entries:
            op  = e.operation
            oper = e.operator
            wc   = e.workcenter
            po   = op.routing.production_order if op and op.routing else None

            duration_h = round(
                (e.scheduled_end - e.scheduled_start).total_seconds() / 3600, 2
            )

            writer.writerow({
                "scenario_name":       scenario.name,
                "sap_order_id":        po.sap_order_id if po else "",
                "order_description":   po.description if po else "",
                "sap_operation_id":    op.sap_operation_id if op else "",
                "operation_description": op.description if op else "",
                "operation_type":      op.operation_type.value if op else "",
                "operator_name":       oper.full_name if oper else "",
                "operator_skill":      oper.skill.value if oper else "",
                "workcenter":          wc.code if wc else "",
                "scheduled_start":     e.scheduled_start.isoformat(),
                "scheduled_end":       e.scheduled_end.isoformat(),
                "duration_hours":      duration_h,
                "status":              e.status.value,
                "delay_minutes":       e.delay_minutes,
            })

        yield buf.getvalue().encode("utf-8-sig")

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="schedule_{scenario_id}.csv"'
        },
    )


# ── JSON-SAP export ───────────────────────────────────────────────────────────

@router.get("/scenario/{scenario_id}/json-sap")
async def export_json_sap(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Export schedule as SAP-ready JSON."""
    scenario = await _get_scenario(scenario_id, db)
    entries  = await _load_entries(scenario_id, db)

    schedule_rows = []
    for e in entries:
        op   = e.operation
        oper = e.operator
        wc   = e.workcenter
        po   = op.routing.production_order if op and op.routing else None

        duration_min = int(
            (e.scheduled_end - e.scheduled_start).total_seconds() // 60
        )

        schedule_rows.append({
            "sap_order_id":     po.sap_order_id if po else None,
            "sap_operation_id": op.sap_operation_id if op else None,
            "resource_id":      oper.employee_id if oper else None,
            "workcenter_code":  wc.code if wc else None,
            "planned_start":    e.scheduled_start.isoformat(),
            "planned_end":      e.scheduled_end.isoformat(),
            "duration_minutes": duration_min,
            "status":           e.status.value,
        })

    return JSONResponse({
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario": {
            "name":         scenario.name,
            "objective":    scenario.objective_mode.value,
            "machine_order": str(scenario.machine_order_id),
        },
        "schedule": schedule_rows,
    })


# ── PDF export ────────────────────────────────────────────────────────────────

@router.get("/scenario/{scenario_id}/pdf")
async def export_pdf(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Genera un report PDF del piano di scheduling usando reportlab.

    Il PDF contiene:
    - KPI di avanzamento (totale, completate, % completamento)
    - Tabella schedule raggruppata per operatore
    - Elenco componenti mancanti ancora in attesa
    - Elenco ritardi attivi registrati sulla macchina

    Usiamo reportlab (cross-platform, niente dipendenze GTK) invece di WeasyPrint.
    """
    from collections import defaultdict

    # reportlab: libreria PDF cross-platform, non richiede GTK/Cairo come WeasyPrint
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    scenario = await _get_scenario(scenario_id, db)
    entries  = await _load_entries(scenario_id, db)

    # Componenti ancora mancanti (non arrivati)
    mc_result = await db.execute(
        select(MissingComponent)
        .filter(MissingComponent.is_arrived.is_(False))
        .limit(50)
    )
    missing = mc_result.scalars().all()

    # Ritardi registrati sulla macchina di questo scenario
    delay_result = await db.execute(
        select(DelayEvent)
        .filter(DelayEvent.machine_order_id == scenario.machine_order_id)
        .limit(30)
    )
    delays = delay_result.scalars().all()

    # ── KPI ──────────────────────────────────────────────────────────────────
    total     = len(entries)
    completed = sum(1 for e in entries if e.status.value == "COMPLETED")
    pct       = round(completed / max(total, 1) * 100, 1)

    # ── Raggruppamento schedule per operatore ─────────────────────────────────
    # Utile per la stampa: il planner vede subito cosa fa ciascuna risorsa
    by_oper: dict[str, list] = defaultdict(list)
    for e in entries:
        oper_name = e.operator.full_name if e.operator else "—"
        by_oper[oper_name].append(e)

    # ── Costruzione documento reportlab ──────────────────────────────────────
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                               leftMargin=1.5*cm, rightMargin=1.5*cm,
                               topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    NAVY   = colors.HexColor("#1e3a5f")
    LIGHT  = colors.HexColor("#e8edf2")

    title_style = ParagraphStyle("Title", parent=styles["Heading1"],
                                 textColor=NAVY, fontSize=14, spaceAfter=4)
    h2_style    = ParagraphStyle("H2", parent=styles["Heading2"],
                                 textColor=NAVY, fontSize=11, spaceBefore=10, spaceAfter=4)
    body_style  = styles["Normal"]

    story = []

    # Intestazione
    story.append(Paragraph("MES Production Scheduler — Report Schedule", title_style))
    story.append(Paragraph(
        f"<b>Scenario:</b> {scenario.name} &nbsp;|&nbsp; "
        f"<b>Obiettivo:</b> {scenario.objective_mode.value} &nbsp;|&nbsp; "
        f"<b>Esportato:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        body_style,
    ))
    story.append(Spacer(1, 0.4*cm))

    # KPI box come tabella 1×3
    story.append(Paragraph("KPI", h2_style))
    kpi_data = [
        ["Operazioni totali", "Completate", "% Completamento"],
        [str(total), str(completed), f"{pct}%"],
    ]
    kpi_table = Table(kpi_data, colWidths=[5*cm, 5*cm, 5*cm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("BACKGROUND",  (0, 1), (-1, 1), LIGHT),
        ("FONTSIZE",    (0, 1), (-1, 1), 14),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("BOX",         (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID",   (0, 0), (-1, -1), 0.5, colors.grey),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 0.3*cm))

    # Tabella schedule per operatore
    story.append(Paragraph("Schedule per Operatore", h2_style))
    sched_data = [["Op ID", "Descrizione", "Inizio", "Fine", "Stato"]]
    for oper_name, elist in by_oper.items():
        # Riga header per ogni operatore (sfondo azzurro chiaro)
        sched_data.append([oper_name, "", "", "", ""])
        for e in elist:
            op = e.operation
            sched_data.append([
                op.sap_operation_id if op else "—",
                (op.description[:40] if op else "—"),
                e.scheduled_start.strftime("%d/%m/%y %H:%M"),
                e.scheduled_end.strftime("%d/%m/%y %H:%M"),
                e.status.value,
            ])

    col_w = [2.5*cm, 7*cm, 3.2*cm, 3.2*cm, 2.5*cm]
    sched_table = Table(sched_data, colWidths=col_w, repeatRows=1)
    # Stile di base
    ts = TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ALIGN",       (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BOX",         (0, 0), (-1, -1), 0.3, colors.grey),
        ("INNERGRID",   (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ])
    # Evidenzia le righe header-operatore in azzurro chiaro
    row_idx = 1
    for oper_name, elist in by_oper.items():
        ts.add("BACKGROUND", (0, row_idx), (-1, row_idx), LIGHT)
        ts.add("FONTNAME",   (0, row_idx), (-1, row_idx), "Helvetica-Bold")
        ts.add("SPAN",       (0, row_idx), (-1, row_idx))
        row_idx += len(elist) + 1
    sched_table.setStyle(ts)
    story.append(sched_table)
    story.append(Spacer(1, 0.3*cm))

    # Componenti mancanti
    story.append(Paragraph("Componenti Mancanti", h2_style))
    if missing:
        mc_data = [["Materiale", "Descrizione", "Arrivo previsto"]]
        for mc in missing:
            mc_data.append([
                mc.component_material,
                (mc.description or "")[:50],
                str(mc.expected_arrival_date) if mc.expected_arrival_date else "—",
            ])
        mc_table = Table(mc_data, colWidths=[3.5*cm, 10*cm, 4*cm])
        mc_table.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
            ("FONTSIZE",    (0, 0), (-1, -1), 8),
            ("INNERGRID",   (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("BOX",         (0, 0), (-1, -1), 0.3, colors.grey),
            ("TOPPADDING",  (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(mc_table)
    else:
        story.append(Paragraph("Nessun componente mancante.", body_style))
    story.append(Spacer(1, 0.3*cm))

    # Ritardi attivi
    story.append(Paragraph("Ritardi Attivi", h2_style))
    if delays:
        del_data = [["Tipo", "Descrizione", "Dal", "Al"]]
        for d in delays:
            del_data.append([
                d.event_type.value,
                (d.description or "")[:50],
                d.delay_from.strftime("%d/%m/%y"),
                d.delay_until.strftime("%d/%m/%y"),
            ])
        del_table = Table(del_data, colWidths=[3.5*cm, 8*cm, 2.5*cm, 2.5*cm])
        del_table.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
            ("FONTSIZE",    (0, 0), (-1, -1), 8),
            ("INNERGRID",   (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("BOX",         (0, 0), (-1, -1), 0.3, colors.grey),
            ("TOPPADDING",  (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(del_table)
    else:
        story.append(Paragraph("Nessun ritardo attivo.", body_style))

    doc.build(story)
    pdf_bytes = buf.getvalue()

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="schedule_{scenario_id}.pdf"'
        },
    )
