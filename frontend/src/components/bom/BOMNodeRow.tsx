import type { BOMTreeNode } from '../../api/types';
import { STATUS_COLORS, STATUS_LABELS, LEVEL_ICONS } from './bomConstants';
import { ChevronRight, ChevronDown, CheckSquare } from 'lucide-react';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';

interface BOMNodeRowProps {
  node: BOMTreeNode;
  depth: number;
  isSelected: boolean;
  onSelect: (node: BOMTreeNode) => void;
  missingMap: Record<string, { id: string; expected_arrival_date: string | null }>;
  machineOrderId: string;
}

export default function BOMNodeRow({
  node,
  depth,
  isSelected,
  onSelect,
  missingMap,
  machineOrderId,
}: BOMNodeRowProps) {
  const [expanded, setExpanded] = useState(depth < 2);
  const queryClient = useQueryClient();
  const isComponent = node.level === 'COMPONENT';
  const hasChildren = node.children.length > 0;

  const missing = missingMap[node.material_code];

  const markArrivedMutation = useMutation({
    mutationFn: (missingId: string) =>
      apiClient.patch(`/api/missing-components/${missingId}/mark-arrived`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bom-tree', machineOrderId] });
      queryClient.invalidateQueries({ queryKey: ['missing-components', machineOrderId] });
    },
  });

  return (
    <>
      <div
        className={`flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-accent group
          ${isSelected ? 'bg-accent' : ''}
        `}
        style={{ paddingLeft: `${depth * 20 + 8}px` }}
        onClick={() => {
          if (!isComponent) onSelect(node);
          if (hasChildren) setExpanded((e) => !e);
        }}
      >
        {/* Expand / collapse toggle */}
        {hasChildren ? (
          <span className="text-muted-foreground shrink-0 w-4">
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
        ) : (
          <span className="w-4 shrink-0" />
        )}

        {/* Level icon */}
        <span className="text-base shrink-0">{LEVEL_ICONS[node.level]}</span>

        {/* Codes + description */}
        <div className="flex-1 min-w-0">
          <span className="font-mono text-xs text-muted-foreground mr-1">
            {node.material_code}
          </span>
          <span className="text-sm truncate">{node.description}</span>
        </div>

        {/* Missing badge */}
        {missing && !node.is_purchase_component && (
          <span className="shrink-0 text-xs bg-orange-100 text-orange-700 border border-orange-300 rounded px-1.5 py-0.5">
            MANCANTE{missing.expected_arrival_date ? ` ${missing.expected_arrival_date}` : ''}
          </span>
        )}

        {/* Status badge */}
        <span
          className={`shrink-0 text-xs border rounded px-1.5 py-0.5 ${STATUS_COLORS[node.status]}`}
        >
          {STATUS_LABELS[node.status]}
        </span>

        {/* Mark arrived button for components with missing component */}
        {isComponent && missing && !node.is_purchase_component && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              markArrivedMutation.mutate(missing.id);
            }}
            disabled={markArrivedMutation.isPending}
            title="Segna come arrivato"
            className="shrink-0 text-green-600 hover:text-green-800 disabled:opacity-50"
          >
            <CheckSquare size={16} />
          </button>
        )}
      </div>

      {/* Children */}
      {expanded &&
        node.children.map((child) => (
          <BOMNodeRow
            key={child.id}
            node={child}
            depth={depth + 1}
            isSelected={isSelected && false /* parent handles selection */}
            onSelect={onSelect}
            missingMap={missingMap}
            machineOrderId={machineOrderId}
          />
        ))}
    </>
  );
}
