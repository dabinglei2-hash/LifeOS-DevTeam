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
  current_status: string;
  heartbeat_connected: boolean;
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
  { label: "Agent 状态", icon: "agents", enabled: true },
  { label: "任务流转", icon: "tasks", enabled: true },
  { label: "成本", icon: "tokens", enabled: true },
  { label: "QA", icon: "qa", enabled: true },
  { label: "GitHub", icon: "repo", enabled: true },
  { label: "知识库", icon: "knowledge", enabled: true },
  { label: "系统设置", icon: "alerts", enabled: true },
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

function Navigation({ mobile = false, active, choose }: { mobile?: boolean; active: string; choose: (value: string) => void }) {
  return (
    <>
      {NAV_ITEMS.map((item) => (
        <button
          className={`${mobile ? "mobile-nav-item" : "nav-item"} ${active === item.icon ? "is-active" : ""}`}
          type="button"
          key={item.label}
          aria-current={active === item.icon ? "page" : undefined}
          onClick={() => choose(item.icon)}
          title={item.label}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
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
        <strong>{agent.current_status.toUpperCase()}</strong>
        <span>{agent.heartbeat_connected ? "已接收进程事件" : "等待可信心跳"}</span>
      </div>
      <p className="agent-note">{agent.status_note}</p>
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

type MonitorRow = Record<string, any>;
const SECTIONS: Record<string, string> = { agents: "agents", tasks: "tasks", tokens: "cost", qa: "qa", repo: "github", knowledge: "knowledge", alerts: "settings" };
const TITLES: Record<string, string> = { agents: "Agent 运行状态", tasks: "任务与时间线", tokens: "Token 与成本", qa: "QA 与审查", repo: "GitHub 双仓库", knowledge: "工程知识库", alerts: "系统设置" };

function Evidence({ value, label = "查看记录详情" }: { value: unknown; label?: string }) {
  return <details className="evidence"><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
function SmallFields({ values }: { values: Record<string, unknown> }) {
  return <dl className="monitor-fields">{Object.entries(values).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value == null ? "UNKNOWN" : String(value)}</dd></div>)}</dl>;
}

function MonitorPage({ name, project, refreshKey }: { name: string; project: ProjectFilter; refreshKey: number }) {
  const [data, setData] = useState<MonitorRow | null>(null);
  const [error, setError] = useState(false);
  const [query, setQuery] = useState("");
  const [task, setTask] = useState("");
  const [agent, setAgent] = useState("");
  const [model, setModel] = useState("");
  const [period, setPeriod] = useState("all");
  const [offset, setOffset] = useState(0);
  const [events, setEvents] = useState<MonitorRow[]>([]);
  const [streamState, setStreamState] = useState("连接中");
  const [tick, setTick] = useState(0);
  const section = SECTIONS[name];
  useEffect(() => { setTask(""); setOffset(0); setData(null); setEvents([]); }, [project, name]);
  useEffect(() => {
    const source = new EventSource(`/api/events?project=${project}`);
    source.onopen = () => setStreamState("已连接");
    source.onerror = () => setStreamState("重连中");
    source.addEventListener("telemetry", (event) => {
      try {
        const row = JSON.parse((event as MessageEvent).data) as MonitorRow;
        setEvents((old) => [row, ...old.filter((item) => item.event_id !== row.event_id)].slice(0, 30));
      } catch { setStreamState("无效事件"); }
    });
    const timer = window.setInterval(() => setTick((value) => value + 1), 5_000);
    return () => { source.close(); window.clearInterval(timer); };
  }, [project]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ project, q: query, agent, model, limit: "30", offset: String(offset) });
    if (task) params.set("task_id", task);
    if (period !== "all") {
      const now = new Date();
      // Shanghai calendar boundaries, independent of the browser timezone.
      const shifted = new Date(now.getTime() + 8 * 3600_000);
      const day = shifted.toISOString().slice(0, 10);
      params.set("since", `${period === "month" ? `${day.slice(0, 7)}-01` : day}T00:00:00+08:00`);
      params.set("until", now.toISOString());
    }
    void fetch(`/api/sections/${section}?${params}`, { signal: controller.signal, cache: "no-store" })
      .then((response) => { if (!response.ok) throw new Error("Unavailable"); return response.json(); })
      .then((value: MonitorRow) => { setData(value); setError(false); })
      .catch((caught: unknown) => { if (!(caught instanceof DOMException && caught.name === "AbortError")) setError(true); });
    return () => controller.abort();
  }, [section, project, task, query, agent, model, period, offset, refreshKey, tick]);
  const items: MonitorRow[] = data?.items ?? [];
  return <div className="monitor-page">
    <section className="panel">
      <div className="panel-heading"><h2>{TITLES[name]}</h2><span className="mini-status">实时事件 · {streamState}</span></div>
      {error && <p className="stale-banner" role="alert">本次刷新失败，已有快照可能过期。</p>}
      {data?.errors?.length > 0 && <p role="alert">部分工程来源不可用；这些项目的数据没有计为零。</p>}
      {(name === "knowledge" || name === "tokens") && <div className="monitor-filters">
        {name === "knowledge" ? <label>知识关键词<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工程文档" maxLength={160} /></label> : <>
          <label>Agent<select value={agent} onChange={(event) => { setAgent(event.target.value); setOffset(0); }}><option value="">全部</option>{["gpt-controller", "scanner", "reviewer", "builder", "qa-tester"].map((role) => <option key={role}>{role}</option>)}</select></label>
          <label>模型<input value={model} onChange={(event) => { setModel(event.target.value); setOffset(0); }} placeholder="精确模型名称" maxLength={100} /></label>
          <label>时间<select value={period} onChange={(event) => { setPeriod(event.target.value); setOffset(0); }}><option value="all">全部记录</option><option value="today">今天</option><option value="month">本月</option></select></label>
          <label>任务 ID<input value={task} onChange={(event) => { setTask(event.target.value); setOffset(0); }} placeholder="完整 UUID" maxLength={36} /></label>
        </>}
      </div>}
      {task && name === "tasks" && <button className="refresh-button" onClick={() => setTask("")}>返回任务列表</button>}
      {!data && !error && <p role="status">正在读取真实工程来源…</p>}
      {data && <p className="panel-note">{data.note ?? "来源：工程运行记录与现有只读接口"}</p>}
      {name === "tokens" && data && <SmallFields values={{ "输入 Token（本页已知）": data.input_tokens_page, "输出 Token（本页已知）": data.output_tokens_page, "实际费用": data.cost_status, "价格配置": data.pricing?.status ?? (data.pricing?.prices?.length ? "已配置" : "未配置") }} />}
      {name === "qa" && data && <SmallFields values={{ "测试运行记录": data.summary?.run_records, "通过": data.summary?.passed, "失败": data.summary?.failed, "其他状态": data.summary?.excluded, "通过率": data.summary?.pass_rate == null ? "UNKNOWN" : `${data.summary.pass_rate}%` }} />}
      {data && !items.length && name !== "alerts" && <p>当前条件没有记录。</p>}
      <div className="monitor-grid">{items.map((row, index) => <article className="monitor-card" key={row.id ?? row.agent_id ?? row.project ?? index}>
        {name === "agents" && <><header><h3>{row.agent_id}</h3><strong className={`live-status state-${row.status}`}>{row.status}</strong></header><SmallFields values={{ Provider: row.provider, "模型": row.sample?.model_name ?? row.history?.model_name, "进程": row.sample?.process_id, "当前任务": row.status === "BUSY" ? row.sample?.task_id : null, "最近心跳/事件": row.sample?.timestamp, "并行工作进程": row.active_runs, "历史 API": row.api_connectivity }} /><p className="micro-note">{row.note}</p><Evidence value={row.sample ?? row.history} label="查看运行证据" /></>}
        {name === "tasks" && <><h3>{row.requirement_summary || row.id}</h3><SmallFields values={{ "任务": row.id, "状态": row.lifecycle, "项目": row.project, Agent: row.assigned_agent, "优先级": row.priority, "更新": row.updated_at, PR: row.pr_ref, "测试": row.test_result, "审查": row.review_result }} /><button className="refresh-button" onClick={() => setTask(row.id)}>查看时间线</button><Evidence value={row} /></>}
        {name === "tokens" && <><h3>{row.model_name}</h3><SmallFields values={{ Agent: row.role, "输入": row.input_tokens, "输出": row.output_tokens, "缓存": row.cached_tokens, "时间": row.created_at, "实际费用": row.cost_status, "估算 USD": row.estimated_cost ? `${row.estimated_cost.amount} · ESTIMATED` : "UNKNOWN", "来源": row.usage_source }} /><Evidence value={row} /></>}
        {name === "qa" && <><header><h3>{row.test_name}</h3><strong>{row.status}</strong></header><SmallFields values={{ "项目": row.project, "开始": row.started_at, "结束": row.completed_at, "任务": row.task_id }} /><p>{row.summary || "没有附加摘要"}</p><Evidence value={row} label="查看测试证据与摘要" /></>}
        {name === "repo" && <><header><h3>{row.repository}</h3><strong>{row.status}</strong></header>{row.snapshot ? <><SmallFields values={{ "来源": "GitHub API", "采样": row.snapshot.sampled_at, "默认分支": row.snapshot.data.defaultBranchRef?.name, "Commit SHA": row.snapshot.data.defaultBranchRef?.target?.oid, "最近 Push": row.snapshot.data.pushedAt, "CI": row.snapshot.data.defaultBranchRef?.target?.statusCheckRollup?.state, "PR 总数": row.snapshot.data.pullRequests?.totalCount, "允许原生自动合并": row.snapshot.data.autoMergeAllowed }} /><a href={row.snapshot.data.url} target="_blank" rel="noreferrer">在 GitHub 打开</a>{row.snapshot.data.pullRequests?.nodes?.map((pr: MonitorRow) => <div className="timeline-item" key={pr.number}><a href={pr.url} target="_blank" rel="noreferrer">#{pr.number} {pr.title}</a><p>{pr.state} · {pr.autoMergeRequest ? "已申请自动合并" : "没有待执行的自动合并"}</p></div>)}</> : <p>尚未取得远程快照，请检查 GitHub 连接。</p>}</>}
        {name === "knowledge" && <><header><h3>{row.project}</h3><strong>{row.status}</strong></header><SmallFields values={{ "数据库": row.database, "迁移": row.migration, "表数": row.table_count, "有效知识": row.count, "索引": row.indexed_at, "备份文件时间": row.latest_backup, "检索实测": row.search_verified }} />{row.search?.items?.map((item: MonitorRow) => <details className="evidence" key={item.id}><summary>{item.title}</summary><p>{item.snippet}</p><SmallFields values={{ "来源": item.source_ref, Commit: item.observed_head }} /></details>)}{row.error_code && <p role="alert">读取失败：{row.error_code}</p>}</>}
      </article>)}</div>
      {name === "alerts" && data && <><SmallFields values={{ "后端": data.backend, "监听地址": data.listen, "数据库": data.database, "迁移版本": data.schema_version, "日志": data.log_directory, "监控": data.monitoring }} /><h3>独立密钥配置</h3><SmallFields values={Object.fromEntries(Object.entries(data.keys_configured ?? {}).map(([role, present]) => [role, present ? "已配置（未验证有效性）" : "未配置"]))} /><h3>系统异常</h3><Evidence value={data.system_events} label="查看最近脱敏错误代码" /></>}
      {["tasks", "qa", "tokens"].includes(name) && data && !task && <div className="monitor-pagination"><button className="refresh-button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span>第 {offset / 30 + 1} 页 · 每项目最多30条</span><button className="refresh-button" disabled={items.length < 30} onClick={() => setOffset(offset + 30)}>下一页</button></div>}
    </section>
    {name === "tasks" && task && <section className="panel"><h2>任务时间线</h2>{[...(data?.events ?? []), ...(data?.historical_events ?? [])].map((row: MonitorRow, index) => <div className="timeline-item" key={row.event_id ?? row.id ?? index}><strong>{row.event_type}</strong><p>{row.timestamp ?? row.occurred_at} · {row.agent_id ?? row.project}</p>{row.payload && <Evidence value={row.payload} label="事件字段" />}</div>)}</section>}
    {name === "qa" && data && <section className="panel"><h2>Reviewer 结论与待解决问题</h2>{data.reviews?.map((row: MonitorRow) => <div className="timeline-item" key={row.id}><strong>{row.review_result}</strong><p>{row.id} · {row.updated_at}</p>{row.error_reason && <p>{row.error_reason}</p>}</div>)}<Evidence value={data.issues} label="查看已记录问题" /></section>}
    {name === "tokens" && data && <section className="panel"><h2>按日期、Agent 与模型汇总</h2><p>按上海自然日统计，应用当前筛选，最多200组。usage记录数不冒充供应商实际请求次数。</p><Evidence value={data.aggregates} label="查看每日用量与已知字段数量" /><h2>尚未与历史对账的实时用量</h2><p>单独显示，避免重复计算；不计为实际费用。</p><Evidence value={data.live_usage} label="查看 Worker 实际返回 usage" /></section>}
    <section className="panel"><h2>实时工程事件</h2><p className="panel-note">SSE · 最近30条 · {streamState}。不展示提示词、原始终端日志或密钥。</p>{events.length ? events.map((event) => <div className="timeline-item" key={event.event_id}><strong>{event.agent_id} · {event.event_type}</strong><p>{event.timestamp} · {event.task_id}</p></div>) : <p>正在等待工程事件，没有生成模拟活动。</p>}</section>
  </div>;
}

export default function App() {
  const [page, setPage] = useState(() => Object.keys(SECTIONS).includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview");
  const [refreshKey, setRefreshKey] = useState(0);
  const choosePage = (value: string) => { location.hash = value; setPage(value); };
  useEffect(() => { const changed = () => setPage(Object.keys(SECTIONS).includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview"); window.addEventListener("hashchange", changed); return () => window.removeEventListener("hashchange", changed); }, []);
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
          <Navigation active={page} choose={choosePage} />
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
            <p className="eyebrow">Local engineering observability</p>
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
              onClick={() => { setRefreshKey((value) => value + 1); void refresh(project, overview === null); }}
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
          <Navigation mobile active={page} choose={choosePage} />
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
          {page !== "overview" ? <MonitorPage name={page} project={project} refreshKey={refreshKey} /> : loading && overview === null ? (
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
          <span>只读监控 · Worker 5s心跳 · 20s过期</span>
          <span>Asia/Shanghai</span>
        </footer>
      </div>
    </div>
  );
}
