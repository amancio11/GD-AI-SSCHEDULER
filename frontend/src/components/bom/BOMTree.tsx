import { useMemo } from 'react';
import type { BOMTreeNode, MissingComponent } from '../../api/types';
import BOMNodeRow from './BOMNodeRow';
import type { BOMFiltersState } from './BOMFilters';

interface BOMTreeProps {
  root: BOMTreeNode;
  selectedId: string | null;
  onSelect: (node: BOMTreeNode) => void;
  filters: BOMFiltersState;
  missingComponents: MissingComponent[];
  machineOrderId: string;
}

/** Flatten a BOMTreeNode tree into a list (depth-first) for filter inspection. */
function flatten(node: BOMTreeNode, acc: BOMTreeNode[] = []): BOMTreeNode[] {
  acc.push(node);
  node.children.forEach((c) => flatten(c, acc));
  return acc;
}

/** Return true if *node* or any descendant matches the filters. */
function nodeMatchesFilters(node: BOMTreeNode, filters: BOMFiltersState): boolean {
  const search = filters.search.toLowerCase();
  const selfMatch =
    (!filters.search ||
      node.material_code.toLowerCase().includes(search) ||
      (node.description?.toLowerCase().includes(search) ?? false)) &&
    (!filters.onlyBlocked || node.status === 'BLOCKED') &&
    (!filters.onlyDelayed || node.status === 'MISSING');

  if (selfMatch) return true;

  // Check descendants recursively
  return node.children.some((child) => nodeMatchesFilters(child, filters));
}

/** Build a map: material_code → {id, expected_arrival_date} */
function buildMissingMap(
  missing: MissingComponent[]
): Record<string, { id: string; expected_arrival_date: string | null }> {
  const map: Record<string, { id: string; expected_arrival_date: string | null }> = {};
  for (const mc of missing) {
    map[mc.component_material] = {
      id: mc.id,
      expected_arrival_date: mc.expected_arrival_date ?? null,
    };
  }
  return map;
}

export default function BOMTree({
  root,
  selectedId,
  onSelect,
  filters,
  missingComponents,
  machineOrderId,
}: BOMTreeProps) {
  const missingMap = useMemo(() => buildMissingMap(missingComponents), [missingComponents]);

  const hasActiveFilters =
    filters.search || filters.onlyBlocked || filters.onlyDelayed || filters.workcenter;

  // If filters are active and root doesn't match, show empty state
  if (hasActiveFilters && !nodeMatchesFilters(root, filters)) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
        Nessun nodo corrisponde ai filtri selezionati.
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto text-sm">
      <BOMNodeRow
        node={root}
        depth={0}
        isSelected={selectedId === root.id}
        onSelect={onSelect}
        missingMap={missingMap}
        machineOrderId={machineOrderId}
      />
    </div>
  );
}
