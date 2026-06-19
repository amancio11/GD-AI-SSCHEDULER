"""CPM Analyzer — Critical Path Method su uno schedule risolto.

Calcola, per ogni operazione schedulata:
  - early_start / early_finish  (forward pass, vincolato dai predecessori)
  - late_start  / late_finish   (backward pass, vincolato dai successori e dal makespan)
  - total_float = late_start - early_start

Un'operazione con total_float == 0 è sul CRITICAL PATH: qualunque ritardo si
propaga 1:1 sul makespan finale. Un'operazione con float > 0 ha un "cuscinetto"
assorbibile senza impattare la data di fine.

INPUT
-----
Il grafo di precedenza è la stessa unione di vincoli usata dal CP-SAT:
  - precedence_pairs: list[(pred_op_id, succ_op_id)]  — precedenze dirette
  - rp_order_constraints espansi: ogni (ops_pred, ops_succ) diventa, ai fini
    del CPM, un arco "virtuale" pred→succ per OGNI coppia (worst case: il
    completamento di TUTTI i pred blocca l'inizio di OGNI succ — coerente con
    la semantica AddMaxEquality + op_start >= completion usata nel solver).

Questo modulo NON richiama OR-Tools: lavora sui tempi già decisi (i
`scheduled_start`/`scheduled_end` salvati in `schedule_entries`), quindi è
molto più veloce di un secondo solve e può girare ad ogni richiesta GET.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import networkx as nx


@dataclass(slots=True)
class CpmResult:
    operation_id: uuid.UUID
    early_start: int  # minuti dall'epoch dello scenario
    early_finish: int
    late_start: int
    late_finish: int
    total_float: int  # minuti di slack assorbibile senza spostare il makespan
    is_critical: bool  # total_float == 0


class CpmAnalyzer:
    """Calcola early/late start-finish e slack per un set di operazioni schedulate."""

    def analyze(
        self,
        *,
        durations_minutes: dict[uuid.UUID, int],
        precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]],
        rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] | None = None,
    ) -> dict[uuid.UUID, CpmResult]:
        """Esegue il forward+backward pass CPM.

        Parametri
        ---------
        durations_minutes:
            Mappa operation_id → durata pianificata in minuti. Definisce
            anche l'universo delle operazioni da analizzare.
        precedence_pairs:
            Archi diretti pred → succ (Meccanismo A del progetto).
        rp_order_constraints:
            Vincoli a gruppi (Meccanismo B): ogni elemento è
            (lista_op_predecessori, lista_op_successori). Vengono espansi in
            archi diretti pred→succ per ciascuna coppia, replicando la
            semantica "tutti i pred devono finire prima che ANY succ inizi".

        Ritorna
        -------
        dict operation_id → CpmResult. Operazioni senza durata nota vengono
        ignorate silenziosamente (non possono entrare nel grafo).
        """
        op_ids = set(durations_minutes.keys())
        if not op_ids:
            return {}

        G = nx.DiGraph()
        for op_id in op_ids:
            G.add_node(op_id)

        edges = list(precedence_pairs)
        if rp_order_constraints:
            for ops_pred, ops_succ in rp_order_constraints:
                for p in ops_pred:
                    for s in ops_succ:
                        edges.append((p, s))

        for pred, succ in edges:
            if pred in op_ids and succ in op_ids and pred != succ:
                G.add_edge(pred, succ)

        # Il grafo DEVE essere aciclico (garantito a monte dal validatore DAG
        # dei reference point); per robustezza, se per qualche motivo contiene
        # un ciclo residuo, lo spezziamo rimuovendo gli archi che lo chiudono
        # piuttosto che far esplodere il calcolo CPM.
        if not nx.is_directed_acyclic_graph(G):
            G = self._break_cycles(G)

        topo_order = list(nx.topological_sort(G))

        # ── Forward pass: early_start / early_finish ──────────────────────────
        early_start: dict[uuid.UUID, int] = {}
        early_finish: dict[uuid.UUID, int] = {}
        for op_id in topo_order:
            preds = list(G.predecessors(op_id))
            es = max((early_finish[p] for p in preds), default=0)
            early_start[op_id] = es
            early_finish[op_id] = es + durations_minutes.get(op_id, 0)

        makespan = max(early_finish.values(), default=0)

        # ── Backward pass: late_start / late_finish ───────────────────────────
        late_finish: dict[uuid.UUID, int] = {}
        late_start: dict[uuid.UUID, int] = {}
        for op_id in reversed(topo_order):
            succs = list(G.successors(op_id))
            lf = min((late_start[s] for s in succs), default=makespan)
            late_finish[op_id] = lf
            late_start[op_id] = lf - durations_minutes.get(op_id, 0)

        # ── Risultato ──────────────────────────────────────────────────────────
        results: dict[uuid.UUID, CpmResult] = {}
        for op_id in topo_order:
            total_float = late_start[op_id] - early_start[op_id]
            results[op_id] = CpmResult(
                operation_id=op_id,
                early_start=early_start[op_id],
                early_finish=early_finish[op_id],
                late_start=late_start[op_id],
                late_finish=late_finish[op_id],
                total_float=total_float,
                is_critical=(total_float <= 0),
            )
        return results

    @staticmethod
    def _break_cycles(G: nx.DiGraph) -> nx.DiGraph:
        """Rimuove archi minimi per rendere G aciclico (fallback difensivo).

        Non dovrebbe mai attivarsi in produzione: il DAG dei reference point è
        validato all'inserimento (vedi reference_points.py — PUT precedences).
        Se capita comunque (es. dati sporchi importati), preferiamo un CPM
        approssimato a un crash totale dell'endpoint.
        """
        G = G.copy()
        while True:
            try:
                cycle = nx.find_cycle(G)
            except nx.NetworkXNoCycle:
                return G
            # Rimuove l'ultimo arco del primo ciclo trovato
            G.remove_edge(*cycle[-1][:2])

    def critical_path_ids(self, results: dict[uuid.UUID, CpmResult]) -> list[uuid.UUID]:
        """Ritorna gli operation_id sul critical path, ordinati per early_start."""
        critical = [r for r in results.values() if r.is_critical]
        critical.sort(key=lambda r: r.early_start)
        return [r.operation_id for r in critical]