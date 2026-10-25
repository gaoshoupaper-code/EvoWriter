import { FormEvent, useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AppShell } from "@/components/workspace/AppShell";
import { ArtifactPanel } from "@/components/workspace/ArtifactPanel";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { ConfirmDialog } from "@/components/workspace/ConfirmDialog";
import { Sidebar } from "@/components/workspace/Sidebar";
import { SidebarFooter } from "@/components/workspace/SidebarFooter";
import { WorkRail } from "@/components/workspace/WorkRail";
import { TopBar } from "@/components/workspace/TopBar";
import { usePanelPolling } from "@/lib/usePanelPolling";
import { projectStageFlow } from "@/lib/stage";
import type { StageFlow } from "@/lib/stage";
import type { WorkspacePanel } from "@/lib/types";
import { useExecutionStore, setExecutionDeps, installExecutionPersist, hasSubstantiveMessages } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { useTraceStore } from "@/stores/trace";
import { useContentStore } from "@/stores/content";
import { useStageSessionStore } from "@/stores/stage-session";
import { useWorkrailStore } from "@/stores/workrail";
import { applyUiFontSize, loadUiFontSize } from "@/components/ui/UiFontSizeControl";

const WORKSPACE_PANELS = ["chat", "artifacts"] as const;

/** 作品栏瞬态重置（review 修复）：跨工作区不携带失败标记/高亮/基线 */
function resetWorkrailTransient() {
  const rail = useWorkrailStore.getState();
  if (rail._highlightTimer) clearTimeout(rail._highlightTimer);
  useWorkrailStore.setState({
    pollError: false,
    highlightKeys: new Set(),
    baselineKeys: null,
    _highlightTimer: null,
  });
}

export default function Home() {
  const navigate = useNavigate();

  // ── 从 stores 取数据（替代原来的 ~55 个 useState）──
  // workspaceStore
  const authChecked = useWorkspaceStore((s) => s.authChecked);
  const authUser = useWorkspaceStore((s) => s.authUser);
  const hasApiKey = useWorkspaceStore((s) => s.hasApiKey);
  const theme = useWorkspaceStore((s) => s.theme);
  const themeReady = useWorkspaceStore((s) => s.themeReady);
  const activePanel = useWorkspaceStore((s) => s.activePanel);
  const setActivePanel = (panel: WorkspacePanel) => useWorkspaceStore.setState({ activePanel: panel });
  // 旧值兜底（FR-006）：activePanel 只在内存流转，历史版本可能残留已删除的
  // 面板值（如 script）——渲染/轮询前回落到 chat，避免整区空白。
  const safePanel: WorkspacePanel = WORKSPACE_PANELS.includes(activePanel) ? activePanel : "chat";
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const activeThreadId = useWorkspaceStore((s) => s.activeThreadId);
  const activeWorkspace = useMemo(
    () => workspaces.find((w) => w.workspace_id === activeWorkspaceId) ?? null,
    [workspaces, activeWorkspaceId],
  );
  const bootstrapping = useWorkspaceStore((s) => s.bootstrapping);
  const creatingWorkspace = useWorkspaceStore((s) => s.creatingWorkspace);
  const deletingWorkspace = useWorkspaceStore((s) => s.deletingWorkspace);
  const workspaceCreateOpen = useWorkspaceStore((s) => s.workspaceCreateOpen);
  const newWorkspaceName = useWorkspaceStore((s) => s.newWorkspaceName);
  const newWorkspaceDomain = useWorkspaceStore((s) => s.newWorkspaceDomain);
  const workspaceDeleteOpen = useWorkspaceStore((s) => s.workspaceDeleteOpen);
  const pendingDeleteWorkspaceId = useWorkspaceStore((s) => s.pendingDeleteWorkspaceId);

  // executionStore
  const messages = useExecutionStore((s) => s.messages);
  const prompt = useExecutionStore((s) => s.prompt);
  const loading = useExecutionStore((s) => s.loading);
  const liveTraceId = useTraceStore((s) => s.liveTraceId);

  // traceStore
  const traceRuns = useTraceStore((s) => s.traceRuns);
  const activeTraceId = useTraceStore((s) => s.activeTraceId);
  const traceDetail = useTraceStore((s) => s.traceDetail);
  const historyDetails = useTraceStore((s) => s.historyDetails);

  // contentStore（五段产物树；旧面板字段经轮询 setter 维持兼容）
  const outlineKind = useContentStore((s) => s.outlineKind);
  const outlinePipeline = useContentStore((s) => s.outlinePipeline);
  const outlineFiles = useContentStore((s) => s.outlineFiles);
  const activeOutlineRel = useContentStore((s) => s.activeOutlineRel);

  const aiDisabled = !hasApiKey;
  const writingDomain = activeWorkspace?.domain !== "image";
  // 技能分组仅五段制写作书显示（REQ-20261025-205943 DEC-011）：用管线 kind
  // 判定（five|legacy），legacy 只读工作区不显示；管线未加载时也不闪现
  const showSidebarSkills =
    !!activeWorkspace && writingDomain && outlinePipeline?.kind === "five";

  // ── 注入 executionDeps（连接三个 store）──
  // 只在首次挂载时注入一次。deps 引用的是 store 的 getState/setState，始终拿到最新值。
  const depsInjected = useRef(false);
  useEffect(() => {
    if (depsInjected.current) return;
    depsInjected.current = true;
    setExecutionDeps({
      getActiveThreadId: () => useWorkspaceStore.getState().activeThreadId,
      getActiveWorkspaceId: () => useWorkspaceStore.getState().activeWorkspaceId,
      getActiveThreadSessionName: () => {
        const ws = useWorkspaceStore.getState();
        return ws.threads.find((t) => t.thread_id === ws.activeThreadId)?.session_name ?? "";
      },
      getActiveThreadWorkspacePath: () => {
        const ws = useWorkspaceStore.getState();
        return ws.threads.find((t) => t.thread_id === ws.activeThreadId)?.workspace_path ?? "";
      },
      getActiveWorkspaceDomain: () => {
        const ws = useWorkspaceStore.getState();
        return ws.workspaces.find((w) => w.workspace_id === ws.activeWorkspaceId)?.domain ?? "writing";
      },
      setActiveThreadId: (id) => useWorkspaceStore.getState().setActiveThreadId(id),
      setThreads: (updater) => useWorkspaceStore.getState().setThreads(updater as any),
      addThread: (thread) => useWorkspaceStore.getState().setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]),
      getTraceRuns: () => useTraceStore.getState().traceRuns,
      getActiveTraceId: () => useTraceStore.getState().activeTraceId,
      getLiveTraceId: () => useTraceStore.getState().liveTraceId,
      setTraceRuns: (updater) => useTraceStore.getState().setTraceRuns(updater as any),
      setTraceDetail: (updater) => useTraceStore.getState().setTraceDetail(updater as any),
      setActiveTraceId: (id) => useTraceStore.getState().setActiveTraceId(id),
      setLiveTraceId: (id) => useTraceStore.getState().setLiveTraceId(id),
    });
    // 本地持久化订阅（REQ-20261001-170627 FR-002）：deps 就绪后安装，幂等
    installExecutionPersist();
  }, []);

  // FR-001（REQ-20261020-193647 review #1/#2）：在指针变化的同步瞬间捕获旧会话
  // 消息——post-render 捕获拿到的是切换后的消息，会把旧会话内存槽位污染成
  // 欢迎语。卸载必须退订：重置/重挂载后残留订阅会把欢迎语写进旧会话槽位。
  // 孤立欢迎语不入槽（与持久化守卫同口径）：槽位消费者不过滤，
  // 欢迎语入槽会遮住完好的磁盘记录。
  useEffect(() => {
    return useWorkspaceStore.subscribe((state, prev) => {
      if (state.activeThreadId === prev.activeThreadId) return;
      if (!prev.activeThreadId) return;
      const exec = useExecutionStore.getState();
      if (!hasSubstantiveMessages(exec.messages)) return;
      exec.threadMessages.set(prev.activeThreadId, exec.messages);
    });
  }, []);

  // ── 主题 ──
  useEffect(() => { useWorkspaceStore.getState().initTheme(); }, []);
  // ── 界面字号(DESIGN-UI.md):启动恢复上次选择 ──
  useEffect(() => { applyUiFontSize(loadUiFontSize()); }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    if (themeReady) window.localStorage.setItem("writer-theme", theme);
  }, [theme, themeReady]);

  // ── 作品栏交付高亮基线（FR-004/AC-006）：任务开始时快照条目集，交付时 diff 出「本次新增」 ──
  const prevLoadingForBaseline = useRef(false);
  useEffect(() => {
    if (loading && !prevLoadingForBaseline.current) {
      useWorkrailStore.getState().captureBaseline();
    }
    prevLoadingForBaseline.current = loading;
  }, [loading]);

  // ── 路由守卫 ──
  useEffect(() => {
    let ignore = false;
    (async () => {
      const ok = await useWorkspaceStore.getState().checkAuth();
      if (ignore || !ok) {
        if (!ignore) navigate("/login", { replace: true });
        return;
      }
    })();
    return () => { ignore = true; };
  }, [navigate]);

  // ── 首次 bootstrap ──
  const initialBootDone = useRef(false);
  const skipNextBootstrap = useRef(false);
  useEffect(() => {
    let ignore = false;
    (async () => {
      resetWorkrailTransient();
      const content = await useWorkspaceStore.getState().bootstrap();
      if (ignore || !content) return;
      useContentStore.getState().setContentData(content);
    })();
    return () => { ignore = true; };
  }, []);

  // ── 后续切换工作区 ──
  useEffect(() => {
    if (!activeWorkspaceId) return;
    if (!initialBootDone.current) { initialBootDone.current = true; return; }
    if (skipNextBootstrap.current) { skipNextBootstrap.current = false; return; }
    let ignore = false;
    (async () => {
      // 瞬态随工作区重置：上一个工作区的失败标记/高亮/基线不得泄漏到新工作区
      resetWorkrailTransient();
      useContentStore.setState({
      });
      const content = await useWorkspaceStore.getState().switchWorkspace(activeWorkspaceId);
      if (ignore || !content) return;
      useContentStore.getState().setContentData(content);
    })();
    return () => { ignore = true; };
  }, [activeWorkspaceId]);

  // ── 会话切换：加载新会话 ──
  // 旧会话消息的保存不在 effect 里做（post-render 拿到的可能已是切换后的
  // 消息，会污染旧会话内存槽位——REQ-20261020-193647 FR-001 review #1/#2），
  // 由 deps 注入处的指针订阅在指针变化的同步瞬间完成
  const prevThreadIdRef = useRef(activeThreadId);
  useEffect(() => {
    if (prevThreadIdRef.current === activeThreadId) return;
    useExecutionStore.getState().loadThreadMessages(activeThreadId);
    prevThreadIdRef.current = activeThreadId;
  }, [activeThreadId]);

  // ── trace 加载（切换 thread 时）──
  useEffect(() => {
    if (!activeThreadId) {
      useTraceStore.getState().clearTrace();
      return;
    }
    useTraceStore.getState().loadTraceRuns(activeThreadId);
  }, [activeThreadId]);

  // ── trace detail 加载（切换 activeTraceId 时）──
  useEffect(() => {
    if (!activeThreadId || !activeTraceId) {
      useTraceStore.getState().setTraceDetail(() => null);
      return;
    }
    useTraceStore.getState().loadTraceDetail(activeThreadId, activeTraceId);
  }, [activeThreadId, activeTraceId, liveTraceId]);

  // ── 历史回放：加载本会话所有 trace 的 detail ──
  const historyTraceKey = traceRuns.map((r) => r.trace_id).join(",");
  useEffect(() => {
    if (!activeThreadId || !historyTraceKey) {
      useTraceStore.setState({ historyDetails: new Map() });
      return;
    }
    const ids = historyTraceKey.split(",").filter(Boolean);
    useTraceStore.getState().loadHistoryDetails(activeThreadId, ids);
  }, [activeThreadId, historyTraceKey]);

  // ── 面板轮询 ──
  usePanelPolling({
    activeWorkspaceId,
    loading,
    bootstrapping,
    setters: {
      setStorylineMarkdown: (v) => useContentStore.getState().setStorylineMarkdown(v),
      setStorylineEntries: (v) => useContentStore.getState().setStorylineEntries(v),
      setActiveStorylineFilename: (fn) => useContentStore.setState((s) => ({ activeStorylineFilename: fn(s.activeStorylineFilename) })),
      setCharacters: (v) => useContentStore.getState().setCharacters(v),
      setActiveCharacterFilename: (fn) => useContentStore.setState((s) => ({ activeCharacterFilename: fn(s.activeCharacterFilename) })),
      setCharactersLoading: (v) => useContentStore.getState().setCharactersLoading(v),
      setObjects: (v) => useContentStore.getState().setObjects(v),
      setActiveObjectFilename: (fn) => useContentStore.setState((s) => ({ activeObjectFilename: fn(s.activeObjectFilename) })),
      setObjectsLoading: (v) => useContentStore.getState().setObjectsLoading(v),
      setWorldviewMarkdown: (v) => useContentStore.getState().setWorldviewMarkdown(v),
      setWorldviewLoading: (v) => useContentStore.getState().setWorldviewLoading(v),
      setStorylinePanorama: (v) => useContentStore.getState().setStorylinePanorama(v),
      setStorylineRhythm: (v) => useContentStore.getState().setStorylineRhythm(v),
      setStorylineFormat: (v) => useContentStore.getState().setStorylineFormat(v),
      setOutline: (v) => useContentStore.getState().setOutline(v),
    },
  });

  // ── stageFlow 派生（从 traceDetail + messages 派生）──
  const stageFlow = useMemo<StageFlow | null>(() => {
    if (!traceDetail) return null;
    const owner = messages.findLast((m) => m.role === "assistant" && m.traceId === traceDetail.run.trace_id);
    return projectStageFlow(traceDetail, owner?.tools ?? []);
  }, [traceDetail, messages]);

  const stageFlows = useMemo<(StageFlow | null)[]>(
    () =>
      messages.map((m) => {
        if (!m.traceId) return null;
        if (m.traceId === traceDetail?.run.trace_id) return stageFlow;
        const histDetail = historyDetails.get(m.traceId);
        return histDetail ? projectStageFlow(histDetail, m.tools ?? []) : null;
      }),
    [messages, traceDetail, stageFlow, historyDetails],
  );

  // ── 阶段制会话（REQ-20261018-175526）──
  // 阶段检测（DEC-013 信号收敛点）：SSE pipeline 事件与 outline 轮询都写
  // outlinePipeline，这里统一对比当前会话绑定阶段 → 挂起完成卡片（FR-004）。
  // 重启/切书后由同不变量自动重建卡片（FR-012，无需独立持久化）。
  useEffect(() => {
    useStageSessionStore.getState().syncWithPipeline(outlinePipeline);
  }, [outlinePipeline, loading, activeThreadId]);

  // 阶段归属恢复：bootstrap/切书后读入该工作区全部会话的阶段绑定（FR-012）
  useEffect(() => {
    if (!activeWorkspaceId) return;
    void useStageSessionStore.getState().hydrateForWorkspace(activeWorkspaceId);
  }, [activeWorkspaceId]);

  // ── 事件处理（委托 store action）──
  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (aiDisabled) {
      toast.error("请先在设置页填写你的 API Key，才能使用 AI 生成。");
      return;
    }
    try {
      if (activeWorkspace?.domain === "image") {
        // 图片域新书没有会话时先自动建一个（旧「新建会话」按钮已随 FR-001 移除）
        if (!activeThreadId) {
          const created = await ensureThreadForWorkspace();
          if (!created) return;
        }
        await useExecutionStore.getState().submitImage({ prompt });
        useExecutionStore.getState().setPrompt("");
        return;
      }
      // 段间切换人工闸（REQ-20261019-232617 FR-002，AC-002）：打字发送不再
      // 隐式切换——消息一律发到当前会话所在段，切段只走就绪卡片/阶段条确认。
      await useExecutionStore.getState().submit(prompt);
      useExecutionStore.getState().setPrompt("");
    } catch {
      /* performSubmit 内部已处理 UI + toast */
    }
  }

  /** 确保当前工作区有会话（图片域新书首轮提交用）；失败返回 false。 */
  async function ensureThreadForWorkspace(): Promise<boolean> {
    const ws = useWorkspaceStore.getState();
    if (!ws.activeWorkspaceId) return false;
    try {
      const { createThread } = await import("@/lib/api");
      const thread = await createThread(ws.activeWorkspaceId, "图片对话");
      ws.setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]);
      ws.setActiveThreadId(thread.thread_id);
      return true;
    } catch (createError) {
      toast.error(createError instanceof Error ? createError.message : "无法开启新对话。");
      return false;
    }
  }

  /** 拍板/访谈选项提交：与 handleSubmit 同构——一律发到当前段（AC-002，不隐式切换）。 */
  async function handleResumeSubmit(resumeText: string) {
    if (aiDisabled) {
      toast.error("请先在设置页填写你的 API Key，才能使用 AI 生成。");
      return;
    }
    await useExecutionStore.getState().resume(resumeText);
  }

  function handleStopGeneration() {
    useExecutionStore.getState().stop();
  }

  function handleRetry() {
    useExecutionStore.getState().retry();
  }

  async function handleCreateWorkspace() {
    skipNextBootstrap.current = true;
    // 「摘指针→重置→清缓存」原子有序（REQ-20261020-193647 FR-001）：
    // 指针未先摘，持久化订阅会把欢迎语写进旧工作区当前会话的 key 覆盖其记录
    useExecutionStore.getState().beginNewWorkspace();
    useContentStore.getState().clearContent();
    await useWorkspaceStore.getState().handleCreateWorkspace();
  }

  async function handleDeleteWorkspace() {
    const pendingId = useWorkspaceStore.getState().pendingDeleteWorkspaceId;
    await useWorkspaceStore.getState().handleDeleteWorkspace();
    // 如果删的是当前工作目录，清空面板
    if (pendingId === activeWorkspaceId) {
      useExecutionStore.getState().clearThreadMessages();
      useExecutionStore.getState().resetMessages();
      useContentStore.getState().clearContent();
      useTraceStore.getState().clearTrace();
    }
  }

  async function handleDeleteThread(threadId: string) {
    // 清理 threadMessages
    useExecutionStore.getState().threadMessages.delete(threadId);
    await useWorkspaceStore.getState().handleDeleteThread(threadId);
    if (activeThreadId === threadId) {
      useTraceStore.getState().clearTrace();
    }
  }

  function handleLogout() {
    useWorkspaceStore.getState().handleLogout(navigate);
  }

  // 未登录：不渲染主界面
  if (!authChecked) return null;

  return (
    <>
      <AppShell
        topBar={<TopBar />}
        sidebar={
          <Sidebar
            activePanel={safePanel}
            onPanelChange={setActivePanel}
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            creatingWorkspace={creatingWorkspace}
            deletingWorkspace={deletingWorkspace}
            onSelectWorkspace={(id) => useWorkspaceStore.getState().setActiveWorkspaceId(id)}
            onCreateWorkspace={() => useWorkspaceStore.setState({ workspaceCreateOpen: true })}
            onDeleteWorkspace={(workspaceId) =>
              useWorkspaceStore.setState({ pendingDeleteWorkspaceId: workspaceId, workspaceDeleteOpen: true })
            }
            files={outlineFiles}
            activeRel={activeOutlineRel}
            onSelectArtifact={(rel) => {
              useContentStore.getState().setActiveOutlineRel(rel);
              useWorkspaceStore.setState({ activePanel: "artifacts" });
            }}
            showSkills={showSidebarSkills}
            footer={
              <SidebarFooter
                theme={theme}
                username={authUser?.username ?? ""}
                hasApiKey={hasApiKey}
                onThemeToggle={() => useWorkspaceStore.getState().toggleTheme()}
                onLogout={handleLogout}
              />
            }
          />
        }
        rail={writingDomain && activeWorkspaceId ? <WorkRail /> : null}
      >
        {safePanel === "chat" ? (
          <ChatPanel
            messages={messages}
            prompt={prompt}
            loading={loading}
            activeThreadId={activeThreadId}
            hasActiveWorkspace={Boolean(activeWorkspaceId)}
            onPromptChange={(p) => useExecutionStore.getState().setPrompt(p)}
            onSubmit={handleSubmit}
            onResumeSubmit={handleResumeSubmit}
            onImageReviewSubmit={async (resume) => {
              if (aiDisabled) { toast.error("请先在设置页填写你的 API Key，才能使用 AI 生成。"); return; }
              await useExecutionStore.getState().submitImage({ resume });
            }}
            onStop={handleStopGeneration}
            stageFlows={stageFlows}
            onRetry={handleRetry}
            onDeleteThread={handleDeleteThread}
          />
        ) : null}

        {safePanel === "artifacts" && activeWorkspaceId ? (
          <ArtifactPanel
            kind={outlineKind}
            pipeline={outlinePipeline}
            files={outlineFiles}
            activeRel={activeOutlineRel}
            onSelect={(rel) => useContentStore.getState().setActiveOutlineRel(rel)}
            workspaceId={activeWorkspaceId}
          />
        ) : null}

      </AppShell>

      {workspaceCreateOpen ? (
        <div className="modal-overlay" role="presentation">
          <section className="modal-content" role="dialog" aria-modal="true" aria-labelledby="workspace-create-title">
            <h2 className="modal-title" id="workspace-create-title">新建工作目录</h2>
            <p className="modal-description">选择类型并输入名称，创建后会直接切换。</p>
            <form
              className="workspace-create-form"
              onSubmit={(event) => { event.preventDefault(); handleCreateWorkspace(); }}
            >
              <div className="workspace-domain-select">
                <label className={`domain-option${newWorkspaceDomain === "writing" ? " selected" : ""}`}>
                  <input type="radio" name="workspace-domain" value="writing" checked={newWorkspaceDomain === "writing"} onChange={() => useWorkspaceStore.setState({ newWorkspaceDomain: "writing" })} disabled={creatingWorkspace} />
                  <span className="domain-option-body"><strong>✍️ 写作</strong><small>小说/剧本创作</small></span>
                </label>
                <label className={`domain-option${newWorkspaceDomain === "image" ? " selected" : ""}`}>
                  <input type="radio" name="workspace-domain" value="image" checked={newWorkspaceDomain === "image"} onChange={() => useWorkspaceStore.setState({ newWorkspaceDomain: "image" })} disabled={creatingWorkspace} />
                  <span className="domain-option-body"><strong>🎨 文生图</strong><small>图片生成与优化</small></span>
                </label>
              </div>
              <input
                className="thread-input workspace-create-input"
                value={newWorkspaceName}
                onChange={(event) => useWorkspaceStore.setState({ newWorkspaceName: event.target.value })}
                placeholder={newWorkspaceDomain === "image" ? "请输入图片主题名" : "请输入新剧本名"}
                autoFocus disabled={creatingWorkspace}
              />
              <div className="modal-actions">
                <button className="modal-button modal-cancel" type="button" onClick={() => useWorkspaceStore.setState({ workspaceCreateOpen: false })} disabled={creatingWorkspace}>取消</button>
                <button className="modal-button modal-primary" type="submit" disabled={creatingWorkspace || !newWorkspaceName.trim()}>
                  {creatingWorkspace ? "创建中" : "创建"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={workspaceDeleteOpen}
        title={`删除工作目录「${workspaces.find((w) => w.workspace_id === pendingDeleteWorkspaceId)?.title || ""}」？`}
        description="这会删除该工作目录以及目录下的所有创作会话。该操作不可撤销。"
        confirmLabel="删除工作目录"
        loading={deletingWorkspace}
        onConfirm={handleDeleteWorkspace}
        onCancel={() => useWorkspaceStore.setState({ workspaceDeleteOpen: false, pendingDeleteWorkspaceId: "" })}
      />

    </>
  );
}
