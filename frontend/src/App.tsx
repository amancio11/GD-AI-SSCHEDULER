import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/shared/Layout';
import Dashboard from './pages/Dashboard';
import GanttView from './pages/GanttView';
import BOMExplorer from './pages/BOMExplorer';
import OperatorCalendar from './pages/OperatorCalendar';
import ReferencePointConfig from './pages/ReferencePointConfig';
import ScenarioManager from './pages/ScenarioManager';
import DelayManager from './pages/DelayManager';
import MissingComponents from './pages/MissingComponents';
import AIAssistant from './pages/AIAssistant';
import ExportPage from './pages/ExportPage';
import DBAdmin from './pages/DBAdmin';
import DAGViewer from './pages/DAGViewer';
import DatabaseExplorer from './pages/DatabaseExplorer';
import SchedulerLogic from './pages/SchedulerLogic';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route element={<Layout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/gantt" element={<GanttView />} />
          <Route path="/bom" element={<BOMExplorer />} />
          <Route path="/calendar" element={<OperatorCalendar />} />
          <Route path="/reference-points" element={<ReferencePointConfig />} />
          <Route path="/scenarios" element={<ScenarioManager />} />
          <Route path="/delays" element={<DelayManager />} />
          <Route path="/missing" element={<MissingComponents />} />
          <Route path="/ai" element={<AIAssistant />} />
          <Route path="/export" element={<ExportPage />} />
          <Route path="/db-admin" element={<DBAdmin />} />
          <Route path="/dag" element={<DAGViewer />} />
          <Route path="/database" element={<DatabaseExplorer />} />
          <Route path="/scheduler-logic" element={<SchedulerLogic />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
