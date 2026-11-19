import { lazy, Suspense, useEffect } from "react";
import { agentArea, isWritingAgent, withAgent } from "@/lib/writing-agent";
import { HashRouter, Routes, Route, Navigate, useNavigate, useLocation, useParams } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import UpdateBanner from "@/components/UpdateBanner";
import { setUnauthorizedHandler } from "@/lib/api";
import Login from "@/pages/login";
import EvolutionConfigPage from "@/pages/config/EvolutionConfigPage";
import ExecutorConfigPage from "@/pages/config/ExecutorConfigPage";
import JudgeGroupPage from "@/pages/config/JudgeGroupPage";
import Monitor from "@/pages/monitor";
import Evolve from "@/pages/evolve";
import ReviewReport from "@/pages/review-report";
import Harness from "@/pages/harness";
import Versions from "@/pages/versions";
import BenchWorkbench from "@/pages/bench/Workbench";
import BenchRules from "@/pages/bench/Rules";
import BenchDataset from "@/pages/bench/Dataset";
import ExperimentDetail from "@/pages/bench/ExperimentDetail";
import ExperimentRecords from "@/pages/bench/ExperimentRecords";
import TraceDetail from "@/pages/trace-detail";
import AdminUsers from "@/pages/admin/users";
import AdminInviteCodes from "@/pages/admin/invite-codes";
import AdminCredits from "@/pages/admin/credits";
import AdminCreditsSettings from "@/pages/admin/credits/settings";
import Shell from "@/components/Shell";
import AdminLayout from "@/components/AdminLayout";

const Analysis = lazy(() => import("@/pages/analysis"));

function UnauthorizedHandler() {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (location.pathname !== "/login") {
        navigate("/login", { replace: true, state: { returnTo: location.pathname + location.search + location.hash } });
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, location]);
  return null;
}

function LegacyAgentLink() {
  const { agent } = useParams();
  const { pathname, search, hash, state } = useLocation();
  if (!isWritingAgent(agent)) return <Navigate to="/" replace />;
  const page = pathname.replace(/^\/agents\/[^/]+/, "") || "/";
  if (page.startsWith("//")) return <Navigate to="/" replace />;
  return <Navigate to={(agentArea(page) ? withAgent(page + search, agent) : page + search) + hash}
    replace state={state} />;
}

function MovedPage({ to }: { to: string }) {
  const { search, hash, state } = useLocation();
  return <Navigate replace to={to + search + hash} state={state} />;
}

function App() {
  return (
    <HashRouter>
      <div className="evolution-app">
        <UpdateBanner />
        <UnauthorizedHandler />
        <Routes>
          <Route path="/agents/:agent/*" element={<LegacyAgentLink />} />
          <Route path="/login" element={<Login />} />
          <Route element={<Shell />}>
            {/* 基础 8 项（D24：不动） */}
            <Route path="/" element={<Monitor />} />
            <Route path="/analysis" element={<Suspense fallback={<div className="page-loading">加载分析…</div>}><Analysis /></Suspense>} />
            <Route path="/evolve" element={<Evolve />} />
            <Route path="/evolve/:sessionId/review" element={<ReviewReport />} />
            <Route path="/harness" element={<Harness />} />
            <Route path="/versions" element={<Versions />} />
            <Route path="/history" element={<MovedPage to="/" />} />

            {/* 评测板块（REQ-20260921-135543 FR-001：独立分组 4 入口 + 旧路由重定向） */}
            <Route path="/bench" element={<BenchWorkbench />} />
            <Route path="/bench/experiments" element={<ExperimentRecords />} />
            <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
            <Route path="/bench/rules" element={<BenchRules />} />
            <Route path="/bench/dataset" element={<BenchDataset />} />
            <Route path="/benchmark" element={<MovedPage to="/bench" />} />
            <Route path="/dataset" element={<MovedPage to="/bench/dataset" />} />

            {/* 配置拆两个（D17），老 /config 重定向到 /config/evolution 保兼容；
                评测组页 REQ-20260930-162207/DEC-010 */}
            <Route path="/config/evolution" element={<EvolutionConfigPage />} />
            <Route path="/config/executor" element={<ExecutorConfigPage />} />
            <Route path="/config/judge-groups" element={<JudgeGroupPage />} />
            <Route path="/config" element={<MovedPage to="/config/evolution" />} />

            {/* 管理后台嵌套 Layout（D11 + D13），仅超管可见（守卫在 Shell.tsx 菜单层） */}
            <Route path="/admin" element={<AdminLayout />}>
              <Route index element={<Navigate to="/admin/users" replace />} />
              <Route path="users" element={<AdminUsers />} />
              <Route path="invite-codes" element={<AdminInviteCodes />} />
              <Route path="credits" element={<AdminCredits />} />
              <Route path="credits/settings" element={<AdminCreditsSettings />} />
            </Route>

            <Route path="/traces/:traceId" element={<TraceDetail />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <Toaster />
      </div>
    </HashRouter>
  );
}

export default App;
