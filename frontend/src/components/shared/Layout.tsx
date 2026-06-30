import { Outlet, NavLink, useLocation } from 'react-router-dom';
import { useEffect } from 'react';
import {
  LayoutDashboard,
  BarChart3,
  GitBranch,
  Calendar,
  Layers,
  AlertTriangle,
  PackageX,
  Bot,
  Download,
  ChevronLeft,
  ChevronRight,
  Sun,
  Moon,
  Wifi,
  WifiOff,
  Database,
  GitFork,
  Cpu,
  Boxes,
} from 'lucide-react';
import { useUiStore } from '../../store/uiStore';
import { useAiStore } from '../../store/aiStore';
import { useMachineStore } from '../../store/machineStore';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useMachineOrders } from '../../api/hooks/useOrders';
import ToastContainer from './ToastContainer';
import AISidebar from './AISidebar';

const NAV_ITEMS = [
  { to: '/dashboard',        label: 'Dashboard',         icon: LayoutDashboard },
  { to: '/gantt',            label: 'Gantt',              icon: BarChart3 },
  { to: '/bom',              label: 'BOM Explorer',       icon: GitBranch },
  { to: '/calendar',         label: 'Calendario',         icon: Calendar },
  { to: '/resources',        label: 'Risorse',            icon: Boxes },
  { to: '/scenarios',        label: 'Scenari',            icon: Layers },
  { to: '/delays',           label: 'Ritardi',            icon: AlertTriangle },
  { to: '/missing',          label: 'Mancanti',           icon: PackageX },
  { to: '/ai',               label: 'AI Assistant',       icon: Bot },
  { to: '/export',           label: 'Export',             icon: Download },
  { to: '/db-admin',         label: 'DB Admin',           icon: Database },
   { to: '/dag',             label: 'DAG Viewer',          icon: GitFork },
  { to: '/database',        label: 'DB Explorer',         icon: Database },
  { to: '/scheduler-logic', label: 'Logica Scheduler',    icon: Cpu },
  { to: '/simulator',       label: 'Operation Simulator', icon: Cpu },
];

function Breadcrumb() {
  const location = useLocation();
  const current = NAV_ITEMS.find((n) => location.pathname.startsWith(n.to));
  return (
    <nav className="text-sm text-muted-foreground" aria-label="breadcrumb">
      <span>MES Scheduler</span>
      {current && (
        <>
          <span className="mx-2">/</span>
          <span className="text-foreground font-medium">{current.label}</span>
        </>
      )}
    </nav>
  );
}

export default function Layout() {
  const { sidebarCollapsed, theme, websocketConnected, toggleSidebar, toggleTheme } = useUiStore();
  const { unreadCount } = useAiStore();
  const { selectedMachineOrderId, setSelectedMachineOrderId } = useMachineStore();
  const { data: machineOrders } = useMachineOrders();

  // Seleziona automaticamente la prima macchina disponibile al primo caricamento
  useEffect(() => {
    if (!selectedMachineOrderId && machineOrders && machineOrders.length > 0) {
      setSelectedMachineOrderId(machineOrders[0].id);
    }
  }, [machineOrders, selectedMachineOrderId, setSelectedMachineOrderId]);

  // Avvia la connessione WebSocket per l'ordine macchina selezionato.
  // La room 'global' è sempre attiva come fallback per notifiche generali.
  useWebSocket(selectedMachineOrderId ?? 'global');

  return (
    <div className={`flex h-screen overflow-hidden ${theme === 'dark' ? 'dark' : ''}`}>
      {/* ── Sidebar ─────────────────────────────────────────────────── */}
      <aside
        className={`flex flex-col bg-card border-r border-border transition-all duration-200 ${
          sidebarCollapsed ? 'w-14' : 'w-56'
        }`}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 px-3 py-4 border-b border-border">
          <span className="text-primary font-bold text-lg shrink-0">⚙</span>
          {!sidebarCollapsed && (
            <span className="font-semibold text-sm truncate">MES Scheduler</span>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-2">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 mx-1 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                }`
              }
              title={sidebarCollapsed ? label : undefined}
            >
              <Icon size={16} className="shrink-0" />
              {!sidebarCollapsed && <span className="truncate">{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Collapse toggle */}
        <button
          onClick={toggleSidebar}
          className="flex items-center justify-center p-3 border-t border-border text-muted-foreground hover:text-foreground"
          aria-label={sidebarCollapsed ? 'Espandi sidebar' : 'Comprimi sidebar'}
        >
          {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </aside>

      {/* ── Main area ───────────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Header */}
        <header className="flex items-center justify-between px-4 py-2 border-b border-border bg-card h-12 shrink-0">
          <Breadcrumb />

          <div className="flex items-center gap-3">
            {/* Machine selector — carica le macchine dal backend */}
            <select
              className="text-sm border border-border rounded px-2 py-1 bg-background text-foreground max-w-[220px]"
              value={selectedMachineOrderId ?? ''}
              onChange={e => setSelectedMachineOrderId(e.target.value || null)}
            >
              {!machineOrders && (
                <option value="">Caricamento…</option>
              )}
              {machineOrders?.map(m => (
                <option key={m.id} value={m.id}>
                  {m.sap_order_id} — {m.description ?? 'Macchina'}
                </option>
              ))}
            </select>

            {/* AI badge */}
            <NavLink
              to="/ai"
              className="relative text-muted-foreground hover:text-foreground"
              title="AI Suggestions"
            >
              <Bot size={18} />
              {unreadCount > 0 && (
                <span className="absolute -top-1 -right-1 bg-destructive text-destructive-foreground text-xs rounded-full w-4 h-4 flex items-center justify-center">
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </NavLink>

            {/* WebSocket indicator */}
            <span
              title={websocketConnected ? 'WebSocket connesso' : 'WebSocket disconnesso'}
              className={websocketConnected ? 'text-green-500' : 'text-destructive'}
            >
              {websocketConnected ? <Wifi size={16} /> : <WifiOff size={16} />}
            </span>

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Cambia tema"
            >
              {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto bg-background">
          <Outlet />
        </main>
      </div>

      <ToastContainer />
      <AISidebar />
    </div>
  );
}
