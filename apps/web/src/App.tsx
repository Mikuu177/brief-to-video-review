import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BellDot,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  FileCheck2,
  Files,
  FolderOpen,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Menu,
  Plus,
  ScanLine,
  Settings2,
  ShieldCheck,
  Upload,
  UsersRound,
  Video,
  X,
} from "lucide-react";
import { api, post } from "./api/client";
import { ActionErrorContext } from "./components/Common";
import type { Asset, Config, Project, ProjectData, User } from "./api/types";
import {
  Badge,
  Empty,
  FileIcon,
  Loading,
  Modal,
  date,
  label,
  awaitsAudioVerification,
} from "./components/Common";
import { SourceViewer } from "./components/SourceViewer";
import MaterialsPage from "./pages/MaterialsPage";
import RulesPage from "./pages/RulesPage";
import SubmissionsPage from "./pages/SubmissionsPage";
import ReviewPage from "./pages/ReviewPage";
import ContentPage from "./pages/ContentPage";
import { readWorkspaceRoute, workspaceHash } from "./workspaceRoute";

const nav = [
  { id: "overview", label: "项目概览", icon: LayoutDashboard },
  { id: "materials", label: "素材与原文", icon: Files },
  { id: "rules", label: "审核规范", icon: FileCheck2 },
  { id: "content", label: "视频内容", icon: ScanLine },
  { id: "submissions", label: "达人提交", icon: UsersRound },
  { id: "review", label: "审核工作台", icon: ScanLine },
  { id: "reports", label: "审核报告", icon: CheckCheck },
];
function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("admin"),
    [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="login">
      <section className="login-story">
        <div className="brand">
          <span className="brand-mark">
            <ScanLine size={25} />
          </span>
          <span>
            审阅台<small>CREATOR REVIEW</small>
          </span>
        </div>
        <div>
          <span className="eyebrow">FROM BRIEF TO FINAL CUT</span>
          <h1>
            每个审核判断，
            <br />
            都有据可循。
          </h1>
          <p>
            把品牌要求、达人创作与人工判断连接起来。
            <br />
            从一份 Brief，到可以回看的每一条证据。
          </p>
          <div className="login-flow">
            <span>Brief</span>
            <ArrowRight size={16} />
            <span>脚本</span>
            <ArrowRight size={16} />
            <span>完整视频</span>
            <ArrowRight size={16} />
            <span>人工确认</span>
          </div>
        </div>
        <div className="small login-footer">
          私有工作空间 · 保留原件 · 版本可追溯
        </div>
      </section>
      <section className="login-form">
        <div>
          <span className="eyebrow">YOUR REVIEW WORKSPACE</span>
          <h2>进入审核工作空间</h2>
          <p className="muted">使用工作空间管理员提供的账号登录。</p>
          <form
            className="form-stack"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              try {
                onLogin(await post<User>("/sessions", { username, password }));
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              账号
              <input
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              密码
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && <div className="notice danger">{error}</div>}
            <button className="primary full" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={17} /> : null}
              进入工作空间 <ArrowRight size={16} />
            </button>
          </form>
          <div className="login-help">
            <CircleHelp size={16} />
            <span>
              如需账号或重置密码，请联系工作空间管理员。服务商
              API Key 由服务端单独配置。
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const initialRoute = useRef(readWorkspaceRoute(location.hash)).current;
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null),
    [initializing, setInitializing] = useState(true),
    [projects, setProjects] = useState<Project[]>([]),
    [projectId, setProjectIdValue] = useState(
      initialRoute.projectId || localStorage.getItem("review-project") || "",
    ),
    [data, setData] = useState<ProjectData | null>(null),
    [config, setConfig] = useState<Config | null>(null),
    [page, setPage] = useState(initialRoute.page),
    [runId, setRunId] = useState(initialRoute.runId),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [creating, setCreating] = useState(false),
    [viewing, setViewing] = useState<{ asset: Asset; focusId?: string } | null>(
      null,
    ),
    [ack, setAck] = useState("");
  const projectIdRef = useRef(projectId);
  const setProjectId = useCallback((id: string, preserveRoute = false) => {
    if (id === projectIdRef.current) return;
    projectIdRef.current = id;
    setProjectIdValue(id);
    setData(null);
    setViewing(null);
    setRunId("");
    setMobileMenuOpen(false);
    if (!preserveRoute)
      history.replaceState(
        null,
        "",
        workspaceHash(readWorkspaceRoute(location.hash).page, id),
      );
  }, []);
  const [projectForm, setProjectForm] = useState({
    name: "",
    brand: "",
    product: "",
    platform: "xiaohongshu",
    phase: "launch",
    mode: "trial",
  });
  const navigate = (next: string, selectedRun = runId) => {
    setPage(next);
    setRunId(selectedRun);
    setMobileMenuOpen(false);
    location.hash = workspaceHash(next, projectIdRef.current, selectedRun);
  };
  useEffect(() => {
    const listener = () => {
      const route = readWorkspaceRoute(location.hash);
      if (route.projectId && route.projectId !== projectIdRef.current)
        setProjectId(route.projectId, true);
      setPage(route.page);
      setRunId(route.runId);
      setMobileMenuOpen(false);
    };
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, [setProjectId]);
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [page, projectId]);
  useEffect(() => {
    api<User>("/session")
      .then(setUser)
      .catch(() => {})
      .finally(() => setInitializing(false));
  }, []);
  const refresh = useCallback(async () => {
    if (!user) return;
    const requestedId = projectIdRef.current;
    const [list, cfg] = await Promise.all([
      api<Project[]>("/projects"),
      api<Config>("/config"),
    ]);
    if (projectIdRef.current !== requestedId) return;
    setProjects(list);
    setConfig(cfg);
    if (requestedId && list.some((p) => p.id === requestedId)) {
      const detail = await api<ProjectData>(`/projects/${requestedId}`);
      if (projectIdRef.current === requestedId) setData(detail);
    } else if (list.length) setProjectId(list[0].id);
    else setData(null);
  }, [user, setProjectId]);
  useEffect(() => {
    if (!user) return;
    void refresh().catch((e) => setError(e.message));
  }, [user, projectId, refresh]);
  useEffect(() => {
    if (projectId) localStorage.setItem("review-project", projectId);
  }, [projectId]);
  const hasActiveRuns = data?.runs.some((r) =>
    ["QUEUED", "RUNNING"].includes(r.status),
  );
  useEffect(() => {
    if (!hasActiveRuns) return;
    const timer = setInterval(
      () => void refresh().catch((e) => setError(e.message)),
      3000,
    );
    return () => clearInterval(timer);
  }, [hasActiveRuns, refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  const act = async (
    work: () => Promise<unknown>,
    message?: string,
    refreshAfter = true,
  ) => {
    setBusy(true);
    setError("");
    try {
      await work();
      if (refreshAfter) await refresh();
      if (message) setToast(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const onRun = (id: string) => {
    navigate("review", id);
  };
  const onAsset = (asset: Asset, focusId?: string) => {
    setAck("");
    setViewing({ asset, focusId });
  };
  if (initializing) return <Loading />;
  if (!user)
    return (
      <Login
        onLogin={(nextUser) => {
          setError("");
          setData(null);
          setProjects([]);
          setUser(nextUser);
        }}
      />
    );
  const props =
    data && config ? { data, config, busy, act, onRun, onAsset } : null;
  const currentPage =
    nav.find((n) => n.id === page)?.label ||
    (page === "settings" ? "模型与服务" : "项目概览");
  const completedVideos =
    data?.runs.filter((r) => r.kind === "video" && r.status === "COMPLETED") ||
    [];
  const navigationContent = (
    <div className="sidebar-content">
      <div className="brand">
        <span className="brand-mark">
          <ScanLine size={25} />
        </span>
        <span>
          审阅台<small>CREATOR REVIEW</small>
        </span>
      </div>
      <div className="workspace-label">
        品牌工作空间 <span>REVIEW</span>
      </div>
      <div className="project-switch">
        <FolderOpen size={17} />
        <select
          aria-label="切换项目"
          value={projectId}
          onChange={(e) => {
            setProjectId(e.target.value);
          }}
        >
          {projects.length ? (
            projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))
          ) : (
            <option value="">尚无项目</option>
          )}
        </select>
        <ChevronDown size={13} />
      </div>
      <nav aria-label="工作空间导航">
        {nav.map((n) => (
          <button
            key={n.id}
            className={page === n.id ? "active" : ""}
            aria-current={page === n.id ? "page" : undefined}
            onClick={() => navigate(n.id)}
          >
            <n.icon size={18} />
            {n.label}
            {n.id === "materials" && data && (
              <small>{data.assets.length}</small>
            )}
            {n.id === "rules" &&
              data?.rulesets[0]?.rules.some(
                (r) => r.confirmation_status === "pending",
              ) && <i />}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="audit-note">
          <ShieldCheck size={20} />
          <strong>AI 初审，人工确认</strong>
          <p>每条结论绑定素材与规范版本，保留完整判断依据。</p>
        </div>
        <button
          className={page === "settings" ? "active" : ""}
          onClick={() => navigate("settings")}
        >
          <Settings2 size={18} />
          模型与服务{!config?.video.ready && <span className="tiny-dot" />}
        </button>
        <div className="account">
          <span className="account-avatar">
            {user.username.slice(0, 1).toUpperCase()}
          </span>
          <div>
            <strong>{user.auth_mode === "local" ? "单用户工作空间" : user.username}</strong>
            <small>{user.auth_mode === "local" ? "本机模式" : user.role === "admin" ? "管理员" : "审核人"}</small>
          </div>
          {user.auth_mode !== "local" && (
          <button
            aria-label="退出登录"
            onClick={() =>
              act(
                async () => {
                  await api("/session", { method: "DELETE" });
                  setData(null);
                  setProjects([]);
                  setConfig(null);
                  setProjectId("");
                  setUser(null);
                },
                undefined,
                false,
              )
            }
          >
            <LogOut size={16} />
          </button>
          )}
        </div>
      </div>
    </div>
  );
  return (
    <ActionErrorContext.Provider value={error}>
      {mobileMenuOpen && (
        <Modal title="工作空间导航" onClose={() => setMobileMenuOpen(false)}>
          <div className="mobile-navigation">{navigationContent}</div>
        </Modal>
      )}
      <div className="app-shell">
        <a
          className="skip-link"
          href="#workspace-content"
          onClick={(event) => {
            event.preventDefault();
            document.getElementById("workspace-content")?.focus();
          }}
        >
          跳至主要内容
        </a>
        <aside className="sidebar desktop-sidebar">{navigationContent}</aside>
        <div className="main-shell">
          <header className="topbar">
            <div>
              <button
                className="mobile-menu-button icon-button"
                aria-label="打开导航"
                aria-expanded={mobileMenuOpen}
                onClick={() => setMobileMenuOpen(true)}
              >
                <Menu size={20} />
              </button>
              <select
                className="mobile-project-switch"
                aria-label="移动端切换项目"
                value={projectId}
                onChange={(e) => {
                  setProjectId(e.target.value);
                }}
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <span className="topbar-crumbs">
                <span>品牌工作空间</span>
                <ChevronDown size={12} />
                <span>{currentPage}</span>
              </span>
            </div>
            <div className="topbar-right">
              <span className="environment-dot" />
              技术试审 · v0.1
              <button
                title="查看模型与服务"
                aria-label="查看模型与服务"
                onClick={() => navigate("settings")}
              >
                <BellDot size={18} />
              </button>
            </div>
          </header>
          <main id="workspace-content" tabIndex={-1}>
            {error && (
              <div className="notice danger global-error" role="alert">
                <span>{error}</span>
                <button aria-label="关闭错误" onClick={() => setError("")}>
                  <X size={16} />
                </button>
              </div>
            )}
            {page === "overview" && (
              <>
                <div className="page-title">
                  <div>
                    <div className="eyebrow">CONTENT REVIEW / OVERVIEW</div>
                    <h1>{data ? data.project.name : "内容审核，从这里开始"}</h1>
                    <p>
                      {data ? (
                        <>
                          {label(data.project.platform)} ·{" "}
                          {label(data.project.phase)} ·{" "}
                          {data.project.mode === "trial"
                            ? "技术试验项目，业务口径待确认"
                            : "正式品牌项目"}
                        </>
                      ) : (
                        "建立一个项目，把品牌要求和达人材料放到同一个审核流程。"
                      )}
                    </p>
                  </div>
                  <div className="actions">
                    <button
                      className="primary"
                      onClick={() => setCreating(true)}
                    >
                      <Plus size={16} />
                      新建项目
                    </button>
                  </div>
                </div>
                {(!projectId || data?.assets.length === 0) && <section className="overview-hero">
                  <div>
                    <span className="hero-kicker">REVIEW WITH EVIDENCE</span>
                    <h2>
                      从品牌要求，
                      <br />
                      到每一条可回看的证据。
                    </h2>
                    <p>
                      先确认 Brief，再审核脚本与完整成片。
                      <br />
                      把重复核对交给 AI，把最终判断留给人。
                    </p>
                    <button
                      className="hero-button"
                      onClick={() =>
                        data ? navigate("materials") : setCreating(true)
                      }
                    >
                      {data ? "查看项目素材" : "创建第一个项目"}
                      <ArrowUpRight size={17} />
                    </button>
                  </div>
                  <div className="workflow-illustration">
                    <div className="workflow-paper">
                      <div className="paper-top">
                        <span className="paper-symbol">
                          <FileCheck2 size={24} />
                        </span>
                        <span>
                          BRAND BRIEF<small>结构化审核规范</small>
                        </span>
                        <CheckCheck size={17} />
                      </div>
                      <div className="paper-lines">
                        <span />
                        <span />
                        <span />
                      </div>
                      <div className="paper-item">
                        <span>01</span>
                        <div>
                          要求有出处<small>页码 · 段落 · 单元格</small>
                        </div>
                        <CheckCheck size={16} />
                      </div>
                      <div className="paper-item">
                        <span>02</span>
                        <div>
                          证据能回看<small>原始声音 · 字幕 · 画面</small>
                        </div>
                        <Video size={16} />
                      </div>
                      <div className="paper-item">
                        <span>03</span>
                        <div>
                          版本可追溯<small>AI 建议与人工决定</small>
                        </div>
                        <ShieldCheck size={16} />
                      </div>
                    </div>
                    <div className="workflow-tag">
                      <span />
                      规则 → 脚本 → 完整视频 → 人工终审
                    </div>
                  </div>
                </section>}
                <div className="overview-stats">
                  <div>
                    <span>原始素材</span>
                    <strong>
                      {data?.assets.length || 0}
                      <small>份</small>
                    </strong>
                    <p>保留文件与内容哈希</p>
                  </div>
                  <div>
                    <span>达人提交</span>
                    <strong>
                      {data?.submissions.length || 0}
                      <small>个版本</small>
                    </strong>
                    <p>素材关联需明确确认</p>
                  </div>
                  <div>
                    <span>已冻结规范</span>
                    <strong>
                      {data?.rulesets.filter((r) => r.status === "FROZEN")
                        .length || 0}
                      <small>版</small>
                    </strong>
                    <p>草案不参与正式审核</p>
                  </div>
                  <div>
                    <span>AI 审核状态</span>
                    <strong className="stat-status">
                      {completedVideos.length
                        ? `已有 ${completedVideos.length} 份成片结果`
                        : config?.video.ready
                          ? "尚无成片结果"
                          : awaitsAudioVerification(config?.video)
                            ? "待音画验收"
                            : "等待配置"}
                    </strong>
                    <p>
                      {completedVideos.length
                        ? awaitsAudioVerification(config?.video)
                          ? "技术试审结果已保存，音画仍待人工验收"
                          : "结果以审核报告为准，最终决定仍需人工确认"
                        : "可在达人提交中发起审核，服务配置与任务结果分别记录"}
                    </p>
                  </div>
                </div>
                <div className="overview-columns">
                  <section className="panel">
                    <div className="panel-heading">
                      <h2>下一步工作</h2>
                      <span className="muted small">按依赖推进</span>
                    </div>
                    {[
                      {
                        title: "整理 Brief 与达人素材",
                        description: "上传原件，查看解析覆盖与来源定位",
                        page: "materials",
                        done: !!data?.assets.length,
                      },
                      {
                        title: "确认审核规范",
                        description: "处理规则范围、冲突与例外，冻结版本",
                        page: "rules",
                        done: !!data?.project.current_ruleset_id,
                      },
                      {
                        title: "审核脚本与完整视频",
                        description: "逐条判断，保留问题依据和原片时间",
                        page: "submissions",
                        done: !!data?.runs.some(
                          (r) =>
                            ["script", "video"].includes(r.kind) &&
                            r.status === "COMPLETED",
                        ),
                      },
                      {
                        title: "人工确认并导出报告",
                        description: "记录最终决定，绑定审核材料版本",
                        page: "reports",
                        done: false,
                      },
                    ].map((s, i) => (
                      <button
                        className="next-step"
                        key={s.title}
                        onClick={() => navigate(s.page)}
                      >
                        <span className={`step-number ${s.done ? "done" : ""}`}>
                          {s.done ? (
                            <CheckCheck size={16} />
                          ) : (
                            String(i + 1).padStart(2, "0")
                          )}
                        </span>
                        <span>
                          <strong>{s.title}</strong>
                          <small>{s.description}</small>
                        </span>
                        <ArrowRight size={16} />
                      </button>
                    ))}
                  </section>
                  <section className="panel recent-panel">
                    <div className="panel-heading">
                      <h2>最近活动</h2>
                      <button
                        className="text-button"
                        onClick={() => navigate("review")}
                      >
                        全部记录 <ArrowRight size={13} />
                      </button>
                    </div>
                    {data?.runs.length ? (
                      data.runs.slice(0, 5).map((r) => (
                        <button
                          className="recent-row"
                          onClick={() => r.kind === "source_reading" ? navigate("rules") : r.kind === "visual_inspection" ? navigate("content",r.content_snapshot_id) : r.kind === "video_content" ? navigate("content",r.id) : onRun(r.id)}
                          key={r.id}
                        >
                          <span className="activity-icon">
                            {r.kind === "parse" ? (
                              <Upload size={16} />
                            ) : (
                              <ScanLine size={16} />
                            )}
                          </span>
                          <span>
                            <strong>
                              {r.kind === "parse"
                                ? "素材解析"
                                : r.kind === "extract"
                                  ? "规范提取"
                                  : r.kind === "source_reading"
                                    ? "来源完整性读取"
                                    : r.kind === "visual_inspection" ? "视频专项检查" : r.kind === "video_content" ? "视频内容读取"
                                    : `${label(r.kind)}审核`}
                            </strong>
                            <small>
                              {date(r.created_at)} · {r.id.slice(0, 8)}
                            </small>
                          </span>
                          <Badge value={r.status} />
                        </button>
                      ))
                    ) : (
                      <Empty
                        title="还没有审核活动"
                        description="导入真实附件，先查看文件解析与原文定位效果。"
                      />
                    )}
                  </section>
                </div>
                {data?.project.mode === "trial" && (
                  <div className="footnote">
                    试验项目中的平台与阶段用于技术演示。历史材料来自 2025
                    年活动，脚本与视频关系以各提交的确认记录为准；不代表品牌批准。
                  </div>
                )}
              </>
            )}
            {page === "materials" && props && <MaterialsPage {...props} />}
            {page === "rules" && props && <RulesPage {...props} />}
            {page === "content" && props && <ContentPage {...props} requestedRunId={runId} />}
            {page === "submissions" && props && <SubmissionsPage {...props} />}
            {page === "review" && props && (
              <ReviewPage {...props} runId={runId} />
            )}
            {page === "reports" && (
              <>
                <div className="page-title">
                  <div>
                    <div className="eyebrow">REVIEW RECORDS</div>
                    <h1>审核报告</h1>
                    <p>查看固定版本的 AI 判断、证据与人工决定。</p>
                  </div>
                </div>
                <div className="panel">
                  {data?.runs.filter((r) =>
                    ["script", "video"].includes(r.kind),
                  ).length ? (
                    data.runs
                      .filter((r) => ["script", "video"].includes(r.kind))
                      .map((r) => (
                        <div className="report-row" key={r.id}>
                          <span className="file-icon">
                            <FileIcon kind={r.kind} />
                          </span>
                          <div>
                            <strong>
                              {label(r.kind)}审核 · {r.id.slice(0, 8)}
                            </strong>
                            <p className="small muted">{date(r.created_at)}</p>
                          </div>
                          <Badge value={r.assessment} />
                          <Badge value={r.freshness} />
                          <button onClick={() => onRun(r.id)}>查看记录</button>
                          <a
                            className="button"
                            target="_blank"
                            rel="noreferrer"
                            href={`/api/v1/runs/${r.id}/report`}
                          >
                            HTML 报告 ↗
                          </a>
                          <a
                            className="text-button"
                            target="_blank"
                            rel="noreferrer"
                            href={`/api/v1/runs/${r.id}/report?format=json`}
                          >
                            JSON
                          </a>
                        </div>
                      ))
                  ) : (
                    <Empty
                      title="还没有审核报告"
                      description="完成 AI 或明确标记的人工独立审核后，报告会保留对应版本的记录。"
                    />
                  )}
                </div>
              </>
            )}
            {page === "settings" && (
              <>
                <div className="page-title">
                  <div>
                    <div className="eyebrow">MODELS & SERVICES</div>
                    <h1>模型与服务</h1>
                    <p>
                      服务端管理凭据，真实模型能力以接口验证与测试结果为准。
                    </p>
                  </div>
                </div>
                <div className="settings-grid">
                  {[
                    {
                      title: "文本与规则审核",
                      icon: FileCheck2,
                      status: config?.text,
                      description: "Brief 规范提取、范围关系与脚本语义判断",
                      model: config?.models.text,
                    },
                    {
                      title: "完整视频与独立 ASR",
                      icon: Video,
                      status: config?.video,
                      description:
                        "原片观察、完整音轨、连续片段、逐规则与证据复核",
                      model: config?.models.video,
                    },
                  ].map((s) => (
                    <section className="panel service-card" key={s.title}>
                      <span className="service-icon">
                        <s.icon size={24} />
                      </span>
                      <div className="section-line">
                        <h2>{s.title}</h2>
                        <Badge
                          value={s.status?.ready ? "COMPLETED" : "pending"}
                        >
                          {s.status?.ready
                            ? "接口已配置"
                            : awaitsAudioVerification(s.status)
                              ? "待音画验收"
                              : "等待配置"}
                        </Badge>
                      </div>
                      <p className="muted">{s.description}</p>
                      <div className="model-name">
                        {s.model || "尚未指定模型"}
                      </div>
                      {s.status?.missing.map((k) => (
                        <div className="config-row" key={k}>
                          <span className="config-dot" />
                          {k === "ARK_VIDEO_AUDIO_VERIFIED" ? (
                            <span>视频音画能力验收</span>
                          ) : (
                            <code>{k}</code>
                          )}
                          <span>
                            {k === "ARK_VIDEO_AUDIO_VERIFIED"
                              ? "待验收"
                              : "待配置"}
                          </span>
                        </div>
                      ))}
                    </section>
                  ))}
                </div>
                <div className="panel panel-pad">
                  <h2>接入后的验证顺序</h2>
                  <ol className="numbered-notes">
                    <li>先验证 36 秒原片的声音和画面，再验证 163 秒原片。</li>
                    <li>核对独立 ASR、时间偏移和播放器证据位置。</li>
                    <li>确认规则与脚本审核，再运行成片完整流水线。</li>
                    <li>
                      使用人工基准统计漏报、误报和不确定比例，保存失败样例。
                    </li>
                  </ol>
                  <div className="notice compact">
                    视频音画验收：
                    {config?.model_live_tested
                      ? "已登记音画 probe，其他能力仍以测试记录为准。"
                      : "尚未通过。接口调用或部分观察结果不等于完整审核能力验收。"}
                  </div>
                </div>
              </>
            )}
            {!data && !["overview", "settings", "reports"].includes(page) && (
              <div className="panel">
                <Empty
                  title={projectId ? "正在读取项目…" : "先创建或选择一个项目"}
                  description={projectId ? "正在加载素材与审核记录。" : "项目保存品牌范围、审核规范、达人提交及全部版本记录。"}
                  action={projectId ? undefined :
                    <button className="primary" onClick={() => setCreating(true)}>新建项目</button>
                  }
                />
              </div>
            )}
          </main>
          <footer className="app-footer">
            <span>审阅台 · 达人内容审核</span>
            <span>原件与判断保留版本 · AI 结果需人工确认</span>
          </footer>
        </div>
        {toast && (
          <div className="toast" role="status">
            <CheckCheck size={18} />
            {toast}
          </div>
        )}
        {busy && (
          <div className="busy-indicator">
            <LoaderCircle size={14} className="spin" />
            正在处理
          </div>
        )}
        {creating && (
          <Modal title="新建品牌审核项目" onClose={() => setCreating(false)}>
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  const p = await post<Project>("/projects", projectForm);
                  setProjectId(p.id);
                  setCreating(false);
                  navigate("materials", "");
                }, "项目已创建");
              }}
            >
              <label>
                项目名称
                <input
                  required
                  value={projectForm.name}
                  onChange={(e) =>
                    setProjectForm({ ...projectForm, name: e.target.value })
                  }
                />
              </label>
              <div className="form-grid">
                <label>
                  品牌
                  <input
                    required
                    value={projectForm.brand}
                    onChange={(e) =>
                      setProjectForm({ ...projectForm, brand: e.target.value })
                    }
                  />
                </label>
                <label>
                  产品
                  <input
                    required
                    value={projectForm.product}
                    onChange={(e) =>
                      setProjectForm({
                        ...projectForm,
                        product: e.target.value,
                      })
                    }
                  />
                </label>
              </div>
              <div className="form-grid">
                <label>
                  平台
                  <select
                    value={projectForm.platform}
                    onChange={(e) =>
                      setProjectForm({
                        ...projectForm,
                        platform: e.target.value,
                      })
                    }
                  >
                    {[
                      "xiaohongshu",
                      "douyin",
                      "bilibili",
                      "other",
                      "unconfirmed",
                    ].map((k) => (
                      <option value={k} key={k}>
                        {label(k)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  活动阶段
                  <select
                    value={projectForm.phase}
                    onChange={(e) =>
                      setProjectForm({ ...projectForm, phase: e.target.value })
                    }
                  >
                    {["launch", "preheat", "unconfirmed"].map((k) => (
                      <option key={k} value={k}>
                        {label(k)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                项目用途
                <select
                  value={projectForm.mode}
                  onChange={(e) =>
                    setProjectForm({ ...projectForm, mode: e.target.value })
                  }
                >
                  <option value="trial">技术试验</option>
                  <option value="formal">正式审核</option>
                </select>
              </label>
              <div className="form-actions">
                <button type="button" onClick={() => setCreating(false)}>
                  取消
                </button>
                <button className="primary" disabled={busy}>
                  创建项目
                </button>
              </div>
            </form>
          </Modal>
        )}
        {viewing && (
          <Modal
            title={viewing.asset.filename}
            wide
            onClose={() => setViewing(null)}
          >
            <SourceViewer
              asset={
                data?.assets.find((a) => a.id === viewing.asset.id) ||
                viewing.asset
              }
              focusId={viewing.focusId}
            />
            {viewing.asset.kind === "brief" && (
              <div className="coverage-ack">
                <label>
                  人工核对覆盖说明
                  <input
                    value={ack}
                    onChange={(e) => setAck(e.target.value)}
                    placeholder="确认已检查图片与未完整解析部分后，记录检查范围"
                  />
                </label>
                <button
                  disabled={busy || ack.length < 10}
                  onClick={() =>
                    act(async () => {
                      await post(`/assets/${viewing.asset.id}/coverage-ack`, {
                        note: ack,
                      });
                      setAck("");
                    }, "人工覆盖核对已记录")
                  }
                >
                  记录核对
                </button>
              </div>
            )}
          </Modal>
        )}
      </div>
    </ActionErrorContext.Provider>
  );
}
