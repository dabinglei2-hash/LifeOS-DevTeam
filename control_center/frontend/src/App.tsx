import { useCallback, useEffect, useRef, useState } from "react";

type ProjectFilter = "all" | "lifeos" | "devteam";
type DataStatus = "known" | "partial" | "error";

interface Metrics {
  active_tasks: number | null;
  completed_today: number | null;
  failed_blocked_today: number | null;
  today_definition: string;
}

interface Tokens {
  total_rows: number | null;
  input_known_rows: number | null;
  output_known_rows: number | null;
  input_unknown_rows: number | null;
  output_unknown_rows: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  semantics: string;
  cost: "unknown";
  cost_note: string;
}

interface QaSummary {
  run_rows: number | null;
  passed: number | null;
  failed: number | null;
  excluded: number | null;
  pass_rate_percent: number | null;
  semantics: string;
  note: string;
}

interface Knowledge {
  active_count: number | null;
  tombstoned_count: number | null;
  latest_indexed_at: string | null;
}

interface Agent {
  id: string;
  label: string;
  family: string;
  current_status: "unknown";
  heartbeat_connected: false;
  status_note: string;
  historical_available: boolean;
  historical_model: string | null;
  historical_provider: "deepseek" | "gpt" | "unknown";
  historical_last_run_at: string | null;
  historical_status: string | null;
  history_note: string;
}

interface Repository {
  project: string;
  label: string;
  repository: string;
  local_available: boolean;
  branch: string | null;
  head: string | null;
  remote_configured: boolean | null;
  remote_status: "unknown";
  last_checked_at: string;
}

interface RecentTask {
  project: string;
  task_id: string | null;
  summary: string;
  state: string;
  assigned_agent: string | null;
  updated_at: string | null;
  completed_at: string | null;
}

interface Source {
  key: string;
  label: string;
  project_id: string | null;
  status: "known" | "error";
  database: string;
}

interface Overview {
  schema: string;
  stage: number;
  read_only: true;
  timezone: "Asia/Shanghai";
  sampled_at: string;
  project_filter: ProjectFilter;
  data_status: DataStatus;
  known_project_count: number;
  selected_project_count: number;
  errors: Array<{ project: string; code: string }>;
  metrics: Metrics;
  tokens: Tokens;
  qa: QaSummary;
  knowledge: Knowledge;
  agents: Agent[];
  repositories: Repository[];
  recent_tasks: RecentTask[];
  recent_complete: boolean;
  sources: Source[];
  limitations: string[];
}

const PROJECT_OPTIONS: Array<{ value: ProjectFilter; label: string }> = [
  { value: "all", label: "全部项目" },
  { value: "lifeos", label: "Life OS" },
  { value: "devteam", label: "DevTeam" },
];

const NAV_ITEMS = [
  { label: "总览", icon: "overview", enabled: true },
  { label: "Agent 状态", icon: "agents", enabled: false, phase: "Phase 2" },
  { label: "任务流转", icon: "tasks", enabled: false, phase: "Phase 3" },
  { label: "成本", icon: "tokens", enabled: false, phase: "Phase 4" },
  { label: "QA", icon: "qa", enabled: false, phase: "Phase 5" },
  { label: "GitHub", icon: "repo", enabled: false, phase: "Phase 6" },
  { label: "知识库", icon: "knowledge", enabled: false, phase: "Phase 7" },
  { label: "系统设置", icon: "alerts", enabled: false, phase: "后续" },
] as const;

const shanghaiTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function formatTimestamp(value: string | null): string {
  if (!value) return "未知";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "未知";
  return shanghaiTime.format(parsed);
}

function formatNumber(value: number | null): string {
  return value === null ? "UNKNOWN" : new Intl.NumberFormat("zh-CN").format(value);
}

function formatRate(value: number | null): string {
  return value === null ? "UNKNOWN" : `${value.toFixed(1)}%`;
}

function scopeLabel(value: ProjectFilter): string {
  return PROJECT_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function stateLabel(value: string): string {
  const labels: Record<string, string> = {
    planned: "已计划",
    active: "进行中",
    testing: "测试中",
    review: "审查中",
    blocked: "已阻塞",
    failed: "失败",
    completed: "完成",
    cancelled: "取消",
    unknown: "未知",
  };
  return labels[value] ?? value;
}

function Icon({ name, size = 19 }: { name: string; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "overview":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7" height="7" rx="2" />
          <rect x="14" y="3" width="7" height="7" rx="2" />
          <rect x="3" y="14" width="7" height="7" rx="2" />
          <rect x="14" y="14" width="7" height="7" rx="2" />
        </svg>
      );
    case "agents":
      return (
        <svg {...common}>
          <circle cx="9" cy="8" r="3" />
          <path d="M3.8 19c.6-3.3 2.3-5 5.2-5s4.6 1.7 5.2 5" />
          <path d="M15.5 7.5a2.5 2.5 0 1 1 2.2 3.8M16.5 14c2.4.2 3.7 1.8 4 4" />
        </svg>
      );
    case "tasks":
      return (
        <svg {...common}>
          <path d="M9 6h11M9 12h11M9 18h11" />
          <path d="m3 6 1.3 1.3L6.5 5M3 12l1.3 1.3L6.5 11M3 18l1.3 1.3L6.5 17" />
        </svg>
      );
    case "qa":
      return (
        <svg {...common}>
          <path d="M8 3h8l3 3v15H5V3h3Z" />
          <path d="m8 13 2.2 2.2L16 9.5" />
        </svg>
      );
    case "knowledge":
      return (
        <svg {...common}>
          <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v17H6.5A2.5 2.5 0 0 0 4 22V5.5Z" />
          <path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v17h4.5A2.5 2.5 0 0 1 20 22V5.5Z" />
        </svg>
      );
    case "repo":
      return (
        <svg {...common}>
          <circle cx="6" cy="5" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="6" cy="19" r="2" />
          <path d="M6 7v10M8 8h3a4 4 0 0 0 4-4M8 16h3a4 4 0 0 1 4 4" />
        </svg>
      );
    case "tokens":
      return (
        <svg {...common}>
          <path d="M5 5h14v14H5z" />
          <path d="M8 9h8M8 12h5M8 15h3" />
        </svg>
      );
    case "alerts":
      return (
        <svg {...common}>
          <path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" />
          <path d="M10 21h4" />
        </svg>
      );
    case "refresh":
      return (
        <svg {...common}>
          <path d="M20 7v5h-5" />
          <path d="M19 12a7 7 0 1 0-2 5" />
        </svg>
      );
    case "shield":
      return (
        <svg {...common}>
          <path d="M12 3 5 6v5c0 4.4 2.8 8 7 10 4.2-2 7-5.6 7-10V6l-7-3Z" />
          <path d="m9 12 2 2 4-4" />
        </svg>
      );
    case "clock":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
      );
    case "warning":
      return (
        <svg {...common}>
          <path d="M12 3 2.8 20h18.4L12 3Z" />
          <path d="M12 9v5M12 17.5v.1" />
        </svg>
      );
    default:
      return null;
  }
}

function Navigation({ mobile = false }: { mobile?: boolean }) {
  return (
    <>
      {NAV_ITEMS.map((item) => (
        <button
          className={`${mobile ? "mobile-nav-item" : "nav-item"} ${item.enabled ? "is-active" : ""}`}
          type="button"
          key={item.label}
          aria-current={item.enabled ? "page" : undefined}
          aria-disabled={!item.enabled}
          disabled={!item.enabled}
          title={item.enabled ? item.label : `${item.label} · ${item.phase} 后续阶段`}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
          {!item.enabled && <small>{item.phase}</small>}
        </button>
      ))}
    </>
  );
}

function StatusPill({ status }: { status: DataStatus | "unknown" | "stale" }) {
  const label =
    status === "known"
      ? "数据可用"
      : status === "partial"
        ? "部分可用"
        : status === "stale"
          ? "数据陈旧"
          : status === "error"
            ? "数据不可用"
            : "状态未知";
  return <span className={`status-pill status-${status}`}>{label}</span>;
}

function LoadingOverview() {
  return (
    <div className="loading-layout" aria-label="正在读取控制中心快照" aria-busy="true">
      <div className="skeleton skeleton-metrics" />
      <div className="agent-grid">
        {Array.from({ length: 5 }, (_, index) => (
          <div className="skeleton skeleton-agent" key={index} />
        ))}
      </div>
      <div className="loading-columns">
        <div className="skeleton skeleton-panel" />
        <div className="skeleton skeleton-panel" />
      </div>
    </div>
  );
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="panel error-panel" role="alert">
      <span className="round-icon">
        <Icon name="warning" />
      </span>
      <div>
        <p className="eyebrow">Snapshot unavailable</p>
        <h2>无法读取最新快照</h2>
        <p>{message} 未显示任何伪造的空值或成功状态。</p>
      </div>
      <button className="secondary-button" type="button" onClick={onRetry}>
        重新读取
      </button>
    </section>
  );
}

function AgentCard({ agent }: { agent: Agent }) {
  const initials = agent.label
    .split(/[\s/]+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <article className="agent-card">
      <header>
        <span className="agent-avatar" aria-hidden="true">
          {initials}
        </span>
        <div>
          <h3>{agent.label}</h3>
          <p>{agent.family}</p>
        </div>
        <StatusPill status="unknown" />
      </header>
      <div className="agent-status-row">
        <span className="status-dot" />
        <strong>UNKNOWN</strong>
        <span>heartbeat 未连接</span>
      </div>
      <p className="agent-note">当前状态未知。{agent.status_note}</p>
      <dl className="agent-history">
        <div>
          <dt>历史模型</dt>
          <dd>{agent.historical_model ?? "UNKNOWN"}</dd>
        </div>
        <div>
          <dt>最后记录运行</dt>
          <dd>
            {agent.historical_last_run_at
              ? formatTimestamp(agent.historical_last_run_at)
              : "无记录"}
          </dd>
        </div>
      </dl>
      <p className="micro-note">{agent.history_note}</p>
    </article>
  );
}

function MetricCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="metric-card">
      <p>{label}</p>
      <strong>{value}</strong>
      <span>{detail}</span>
    </article>
  );
}

function TokenPanel({ tokens }: { tokens: Tokens }) {
  return (
    <section className="panel token-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Usage · historical</p>
          <h2>已记录的 Token</h2>
        </div>
        <span className="outline-badge">可能不完整</span>
      </div>
      <div className="token-values">
        <div>
          <span>输入</span>
          <strong>{formatNumber(tokens.input_tokens)}</strong>
          <small>
            {formatNumber(tokens.input_known_rows)} / {formatNumber(tokens.total_rows)} 行有值
          </small>
        </div>
        <div>
          <span>输出</span>
          <strong>{formatNumber(tokens.output_tokens)}</strong>
          <small>
            {formatNumber(tokens.output_known_rows)} / {formatNumber(tokens.total_rows)} 行有值
          </small>
        </div>
      </div>
      <p className="panel-note">
        汇总仅来自 model_usage 一次；未知或 null 不按 0 处理。成本为
        <strong> UNKNOWN</strong>，不使用 cost_usd 作为账单估计。
      </p>
    </section>
  );
}

function QaPanel({ qa }: { qa: QaSummary }) {
  return (
    <section className="panel qa-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Sealed test runs</p>
          <h2>QA RUN 通过率</h2>
        </div>
        <span className="rate-value">{formatRate(qa.pass_rate_percent)}</span>
      </div>
      <div className="qa-breakdown">
        <div>
          <span>Passed</span>
          <strong>{formatNumber(qa.passed)}</strong>
        </div>
        <div>
          <span>Failed</span>
          <strong>{formatNumber(qa.failed)}</strong>
        </div>
        <div>
          <span>Excluded</span>
          <strong>{formatNumber(qa.excluded)}</strong>
        </div>
      </div>
      <p className="panel-note">{qa.note}</p>
    </section>
  );
}

function KnowledgePanel({ knowledge }: { knowledge: Knowledge }) {
  return (
    <section className="panel knowledge-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Indexed metadata</p>
          <h2>知识库状态</h2>
        </div>
        <span className="round-icon">
          <Icon name="knowledge" />
        </span>
      </div>
      <div className="knowledge-values">
        <div>
          <strong>{formatNumber(knowledge.active_count)}</strong>
          <span>有效条目</span>
        </div>
        <div>
          <strong>{formatNumber(knowledge.tombstoned_count)}</strong>
          <span>已归档</span>
        </div>
      </div>
      <p className="panel-note">
        最近索引：{formatTimestamp(knowledge.latest_indexed_at)}
      </p>
    </section>
  );
}

function RepositoryCard({ repository }: { repository: Repository }) {
  return (
    <article className="repo-card">
      <header>
        <span className="round-icon">
          <Icon name="repo" />
        </span>
        <div>
          <h3>{repository.label}</h3>
          <p>{repository.repository}</p>
        </div>
      </header>
      <dl>
        <div>
          <dt>Branch</dt>
          <dd>{repository.branch ?? "UNKNOWN"}</dd>
        </div>
        <div>
          <dt>Local HEAD</dt>
          <dd className="mono">
            {repository.head ? repository.head.slice(0, 10) : "UNKNOWN"}
          </dd>
        </div>
        <div>
          <dt>Remote status</dt>
          <dd>
            <span className="mini-status">UNKNOWN</span>
          </dd>
        </div>
      </dl>
      <p className="micro-note">仅本地只读 Git 元数据；未 fetch，也不推断远端同步状态。</p>
    </article>
  );
}

function RecentTasks({ tasks, complete }: { tasks: RecentTask[]; complete: boolean }) {
  return (
    <section className="panel recent-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Latest persisted updates</p>
          <h2>最近任务更新</h2>
        </div>
        <span className="outline-badge">{complete ? "最多 20 条" : "部分来源"}</span>
      </div>
      {tasks.length === 0 ? (
        <div className="empty-state">
          <Icon name="tasks" size={24} />
          <strong>当前范围没有任务记录</strong>
          <span>空列表来自已连接数据库，不使用示例数据。</span>
        </div>
      ) : (
        <ol className="task-list">
          {tasks.map((task, index) => (
            <li key={`${task.project}-${task.task_id ?? index}`}>
              <div className="task-main">
                <div className="task-meta">
                  <span className="project-chip">{scopeLabel(task.project as ProjectFilter)}</span>
                  <span className={`task-state state-${task.state}`}>{stateLabel(task.state)}</span>
                </div>
                <p>{task.summary}</p>
              </div>
              <div className="task-time">
                <time dateTime={task.updated_at ?? undefined}>
                  {formatTimestamp(task.updated_at)}
                </time>
                <span>{task.assigned_agent ?? "未记录 Agent"}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function SourcesPanel({ overview }: { overview: Overview }) {
  return (
    <section className="panel sources-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Boundaries</p>
          <h2>来源与限制</h2>
        </div>
        <span className="round-icon">
          <Icon name="shield" />
        </span>
      </div>
      <div className="source-grid">
        <div>
          <h3>只读来源</h3>
          {overview.sources.map((source) => (
            <div className="source-row" key={source.key}>
              <span>{source.label}</span>
              <code>{source.database}</code>
              <StatusPill status={source.status} />
            </div>
          ))}
        </div>
        <div>
          <h3>明确限制</h3>
          <ul>
            {overview.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </div>
      </div>
      {overview.errors.length > 0 && (
        <div className="error-codes">
          {overview.errors.map((error) => (
            <code key={`${error.project}-${error.code}`}>
              {error.project}: {error.code}
            </code>
          ))}
        </div>
      )}
    </section>
  );
}

function OverviewContent({ overview }: { overview: Overview }) {
  return (
    <>
      {overview.data_status !== "known" && (
        <section className="data-banner" role="status">
          <Icon name="warning" />
          <div>
            <strong>
              {overview.data_status === "partial" ? "部分项目来源不可用" : "项目来源不可用"}
            </strong>
            <span>
              已连接 {overview.known_project_count} / {overview.selected_project_count} 个范围；
              聚合指标保持 UNKNOWN，不按 0 展示。
            </span>
          </div>
        </section>
      )}

      <section className="section-block" aria-labelledby="metrics-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Today · Asia/Shanghai</p>
            <h2 id="metrics-heading">今日概览</h2>
          </div>
          <span>{overview.metrics.today_definition}</span>
        </div>
        <div className="metric-grid">
          <MetricCard
            label="进行中任务"
            value={formatNumber(overview.metrics.active_tasks)}
            detail="当前 state=active"
          />
          <MetricCard
            label="今日完成"
            value={formatNumber(overview.metrics.completed_today)}
            detail="完成时间落在上海今天"
          />
          <MetricCard
            label="今日失败 + 阻塞"
            value={formatNumber(overview.metrics.failed_blocked_today)}
            detail="按最后任务更新时间"
          />
          <MetricCard label="成本" value="UNKNOWN" detail="无账单遥测，不做估算" />
        </div>
      </section>

      <section className="section-block" aria-labelledby="agents-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Five roles · no inferred liveness</p>
            <h2 id="agents-heading">Agent 概览</h2>
          </div>
          <span>历史记录不等于当前在线</span>
        </div>
        <div className="agent-grid">
          {overview.agents.map((agent) => (
            <AgentCard agent={agent} key={agent.id} />
          ))}
        </div>
      </section>

      <div className="insight-grid">
        <TokenPanel tokens={overview.tokens} />
        <QaPanel qa={overview.qa} />
        <KnowledgePanel knowledge={overview.knowledge} />
      </div>

      <div className="activity-grid">
        <RecentTasks tasks={overview.recent_tasks} complete={overview.recent_complete} />
        <section className="panel repository-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Canonical roots</p>
              <h2>本地 Git 仓库</h2>
            </div>
            <span className="outline-badge">远端状态未知</span>
          </div>
          <div className="repo-stack">
            {overview.repositories.map((repository) => (
              <RepositoryCard repository={repository} key={repository.project} />
            ))}
          </div>
        </section>
      </div>

      <SourcesPanel overview={overview} />
    </>
  );
}

export default function App() {
  const [project, setProject] = useState<ProjectFilter>("all");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastGoodAt, setLastGoodAt] = useState<number | null>(null);
  const [clock, setClock] = useState(Date.now());
  const projectRef = useRef<ProjectFilter>(project);
  const recentScopeRef = useRef<ProjectFilter | null>(null);
  const requestIdRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const lastAttemptRef = useRef(Date.now());

  const refresh = useCallback(async (target: ProjectFilter, fullScreen = false) => {
    const requestId = ++requestIdRef.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    lastAttemptRef.current = Date.now();
    if (fullScreen || recentScopeRef.current !== target) {
      setLoading(true);
    }
    setRefreshing(true);
    setError(null);

    try {
      const response = await fetch(`/api/overview?project=${encodeURIComponent(target)}`, {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: controller.signal,
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error("snapshot unavailable");
      }
      const payload = (await response.json()) as Overview;
      if (requestId !== requestIdRef.current || projectRef.current !== target) return;
      if (payload.schema !== "lads-control-center-stage1-v1" || payload.read_only !== true) {
        throw new Error("unexpected snapshot contract");
      }
      recentScopeRef.current = target;
      setOverview(payload);
      setLastGoodAt(Date.now());
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (requestId !== requestIdRef.current || projectRef.current !== target) return;
      setError("控制中心 API 或数据来源当前不可用。");
      if (recentScopeRef.current !== target) {
        setOverview(null);
        setLastGoodAt(null);
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    projectRef.current = project;
    recentScopeRef.current = null;
    setOverview(null);
    setLastGoodAt(null);
    setError(null);
    void refresh(project, true);
  }, [project, refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setClock(Date.now());
      if (Date.now() - lastAttemptRef.current >= 15_000) {
        void refresh(projectRef.current, false);
      }
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
    },
    [],
  );

  const stale =
    overview !== null &&
    lastGoodAt !== null &&
    (error !== null || clock - lastGoodAt > 30_000);
  const status: DataStatus | "stale" | "unknown" = stale
    ? "stale"
    : overview?.data_status ?? "unknown";

  const chooseProject = (next: ProjectFilter) => {
    if (next === project) return;
    projectRef.current = next;
    recentScopeRef.current = null;
    setOverview(null);
    setLastGoodAt(null);
    setError(null);
    setProject(next);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">L</span>
          <div>
            <strong>LADS</strong>
            <span>Control Center</span>
          </div>
        </div>
        <p className="nav-caption">研发团队控制中心</p>
        <nav className="side-nav" aria-label="主导航">
          <Navigation />
        </nav>
        <div className="sidebar-note">
          <Icon name="shield" />
          <div>
            <strong>Local only</strong>
            <span>127.0.0.1 · read only</span>
          </div>
        </div>
      </aside>

      <header className="mobile-header">
        <div className="brand">
          <span className="brand-mark">L</span>
          <div>
            <strong>LADS Control Center</strong>
            <span>研发团队控制中心</span>
          </div>
        </div>
        <span className="readonly-badge">
          <Icon name="shield" size={16} />
          只读
        </span>
      </header>

      <div className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Stage 1 · functional overview</p>
            <h1>
              LADS Control Center <span>/ 研发团队控制中心</span>
            </h1>
            <p className="subtitle">两个工程项目的本地只读快照，明确区分已知、未知与未连接。</p>
          </div>
          <div className="topbar-actions">
            <span className="readonly-badge">
              <Icon name="shield" size={16} />
              Read only
            </span>
            <button
              className="refresh-button"
              type="button"
              onClick={() => void refresh(project, overview === null)}
              disabled={loading || refreshing}
              aria-label="刷新控制中心快照"
            >
              <span className={refreshing ? "refresh-icon is-spinning" : "refresh-icon"}>
                <Icon name="refresh" />
              </span>
              <span>{refreshing ? "刷新中" : "刷新"}</span>
            </button>
          </div>
        </header>

        <nav className="mobile-nav" aria-label="移动端主导航">
          <Navigation mobile />
        </nav>

        <section className="control-strip" aria-label="快照范围与状态">
          <div className="project-filter" role="group" aria-label="选择项目范围">
            {PROJECT_OPTIONS.map((option) => (
              <button
                type="button"
                className={project === option.value ? "is-selected" : ""}
                aria-pressed={project === option.value}
                onClick={() => chooseProject(option.value)}
                key={option.value}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="sample-meta">
            <StatusPill status={status} />
            <span>
              <Icon name="clock" size={16} />
              最后采样：{overview ? formatTimestamp(overview.sampled_at) : "等待中"}
            </span>
            <span>范围：{scopeLabel(project)}</span>
          </div>
        </section>

        <main>
          {loading && overview === null ? (
            <LoadingOverview />
          ) : error && overview === null ? (
            <ErrorPanel message={error} onRetry={() => void refresh(project, true)} />
          ) : overview ? (
            <>
              {stale && (
                <div className="stale-banner" role="status">
                  <Icon name="clock" />
                  <span>当前显示最近一次成功快照；刷新失败或超过 30 秒未更新。</span>
                </div>
              )}
              <OverviewContent overview={overview} />
            </>
          ) : null}
        </main>

        <footer className="footer-note">
          <span>Snapshot refresh · 15s</span>
          <span>此轮询不是 Agent heartbeat。</span>
          <span>Asia/Shanghai</span>
        </footer>
      </div>
    </div>
  );
}
