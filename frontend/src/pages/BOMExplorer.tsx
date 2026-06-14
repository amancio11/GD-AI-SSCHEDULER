import { useState } from 'react';
import { useBOMTree } from '../api/hooks/useOrders';
import { useMissingComponents } from '../api/hooks/useMissing';
import { useMachineStore } from '../store/machineStore';
import BOMTree from '../components/bom/BOMTree';
import BOMFilters, { type BOMFiltersState } from '../components/bom/BOMFilters';
import BOMNodeDetail from '../components/bom/BOMNodeDetail';
import type { BOMTreeNode } from '../api/types';

const DEFAULT_MACHINE_ORDER_ID = 'auto'; // replaced by real ID from store

export default function BOMExplorer() {
  const { selectedMachineOrderId } = useMachineStore();
  const machineOrderId = selectedMachineOrderId ?? DEFAULT_MACHINE_ORDER_ID;

  const { data: bomTree, isLoading, isError } = useBOMTree(
    selectedMachineOrderId ?? undefined
  );
  const { data: missingComponents = [] } = useMissingComponents(
    selectedMachineOrderId ?? undefined
  );

  const [selectedNode, setSelectedNode] = useState<BOMTreeNode | null>(null);
  const [filters, setFilters] = useState<BOMFiltersState>({
    search: '',
    onlyBlocked: false,
    onlyDelayed: false,
    workcenter: '',
  });

  // Breadcrumb: just the current selected node path (simplified)
  const breadcrumb = selectedNode
    ? `${selectedNode.level} › ${selectedNode.material_code}`
    : 'Struttura BOM';

  if (!selectedMachineOrderId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Seleziona un ordine macchina dall&apos;header per visualizzare la BOM.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-4 py-2 border-b border-border bg-card">
        <h1 className="text-lg font-semibold">BOM Explorer</h1>
        <p className="text-xs text-muted-foreground">{breadcrumb}</p>
      </div>

      {/* Filters */}
      <BOMFilters filters={filters} onChange={setFilters} workcenters={[]} />

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Tree */}
        <div className="flex-1 overflow-auto">
          {isLoading && (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
              Caricamento BOM…
            </div>
          )}
          {isError && (
            <div className="flex items-center justify-center h-40 text-destructive text-sm">
              Errore nel caricamento della BOM.
            </div>
          )}
          {bomTree && (
            <BOMTree
              root={bomTree}
              selectedId={selectedNode?.id ?? null}
              onSelect={setSelectedNode}
              filters={filters}
              missingComponents={missingComponents}
              machineOrderId={machineOrderId}
            />
          )}
        </div>

        {/* Detail panel */}
        {selectedNode && selectedNode.level !== 'COMPONENT' && (
          <BOMNodeDetail
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>
    </div>
  );
}

