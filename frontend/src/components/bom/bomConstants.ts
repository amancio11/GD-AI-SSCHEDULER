import type { ProductionOrderStatus, ProductionOrderLevel } from '../../api/types';

export const STATUS_COLORS: Record<ProductionOrderStatus, string> = {
  PLANNED: 'bg-gray-100 text-gray-700 border-gray-300',
  IN_PROGRESS: 'bg-blue-100 text-blue-700 border-blue-300',
  COMPLETED: 'bg-green-100 text-green-700 border-green-300',
  BLOCKED: 'bg-red-100 text-red-700 border-red-300',
  MISSING: 'bg-orange-100 text-orange-700 border-orange-300',
};

export const STATUS_LABELS: Record<ProductionOrderStatus, string> = {
  PLANNED: 'Pianificato',
  IN_PROGRESS: 'In lavorazione',
  COMPLETED: 'Completato',
  BLOCKED: 'Bloccato',
  MISSING: 'Mancante',
};

export const LEVEL_ICONS: Record<ProductionOrderLevel, string> = {
  MACHINE: '🏭',
  MACROAGGREGATE: '📦',
  AGGREGATE: '🔧',
  GROUP: '🗂️',
  COMPONENT: '⚙️',
};
