import { useCallback, useEffect, useRef, useState } from "react";
import { Download, RefreshCw, ScanLine } from "lucide-react";
import { api, jobKey, post } from "../api/client";
import type { WorkspaceProps } from "../api/types";
import { time } from "../components/Common";
import { VideoPlayer } from "../components/VideoPlayer";
import "./content-page.css";
import VisualInspectionPanel from "./VisualInspectionPanel";

type Gap = {code:string;reason:string;event_id?:string;unit_id?:string;range_ms?:number[];candidate_ids?:string[];event_ids?:string[]};
type Fact = {
  id: string;
  channel: string;
  raw_content: string;
  start_ms: number;
  end_ms: number;
  status: string;
  actor_id: string | null;
  object_id: string | null;
  speaker_id: string | null;
  action: string | null;
  origin: {
    stage: string;
    reported_channel?: string;
    audio_classification?: string;
    words?: { word: string; start_ms: number; end_ms: number }[];
  };
  unit_ids: string[];
};
type Entity = {
  id: string;
  kind: string;
  description: string;
  identity_level: string;
  basis: string;
  status: string;
  evidence_ids: string[];
};
type Claim = {
  id: string;
  kind: string;
  description: string;
  relation: string;
  status: string;
  evidence_ids: string[];
  requires_recheck?: boolean;
};
type Snapshot = {
  id: string;
  asset_id: string;
  filename: string;
  source_sha256: string;
  status: string;
  content_hash: string | null;
  parent_id: string | null;
  superseded_by: string | null;
  asr_capabilities?: {
    utterances: number;
    with_words: number;
    with_speaker: number;
  } | null;
  run_id: string | null;
  run_status: string | null;
  error: {
    code: string;
    message: string;
    unknown_remote_state?: boolean;
  } | null;
  report: {
    processing_complete: boolean;
    unit_count?: number;
    event_count?: number;
    entity_count?: number;
    claim_count?: number;
    unresolved?: number;
  };
  additional_checks?: {version:string;hash:string;status:string;gaps:Gap[]}|null;
  confirmation: { reason: string } | null;
  correction_log: unknown[];
  payload: {
    duration_ms?: number;
    events?: Fact[];
    entities?: Entity[];
    claims?: Claim[];
    units?: {
      id: string;
      start_ms: number;
      end_ms: number;
      boundary_reason: string;
    }[];
    gaps?: Gap[];
    limits?: string[];
    asr_capabilities?: {
      utterances: number;
      with_words: number;
      with_speaker: number;
    };
  };
  stages: {
    name: string;
    status: string;
    attempt: number;
    error?: { code: string; message: string } | null;
  }[];
};
const statusText: Record<string, string> = {
  INGESTED: "已登记",
  READING: "读取中",
  NEEDS_HUMAN: "待内容核对",
  HUMAN_REVIEWED_WITH_LIMITS: "已记录回放确认",
  QUEUED: "排队中",
  RUNNING: "处理中",
  INCOMPLETE: "处理中断",
  COMPLETED: "阶段完成",
  CANDIDATE: "候选",
  UNCLEAR: "辨认不清",
  NOT_OBSERVED: "未观察到",
  UNKNOWN: "未知",
  DISPUTED: "有分歧",
  FAILED: "本次尝试失败",
};
const channels: Record<string, string> = {
  spoken_audio: "口播",
  onscreen_text: "画面文字",
  visual_event: "画面动作",
  sound_event: "声音/音效",
  unclassified_audio: "未区分声音",
};
export default function ContentPage({
  data,
  act,
  requestedRunId,
}: WorkspaceProps & { requestedRunId?: string }) {
  const projectId = data.project.id;
  function sourceLabel(source: Snapshot){
    const names=[...new Set(data.submissions.filter(s=>s.pairing_confirmed&&s.video_id===source.asset_id).map(s=>s.creator))];
    return names.length===1?names[0]:source.filename;
  }
  const [items, setItems] = useState<Snapshot[]>([]),
    [enabled, setEnabled] = useState<boolean | null>(null),
    [assetId, setAssetId] = useState("");
  const [selected, setSelected] = useState(""),
    [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [trial, setTrial] = useState(false),
    [unknownAck, setUnknownAck] = useState(false),
    [filter, setFilter] = useState("all"),
    [unitFilter, setUnitFilter] = useState(""),
    [query, setQuery] = useState("");
  const [showRaw,setShowRaw]=useState(false);
  const [tab, setTab] = useState("events"),
    [offset, setOffset] = useState(0),
    [active, setActive] = useState<Fact | null>(null),
    [edit, setEdit] = useState(false);
  const [editStatus, setEditStatus] = useState("CANDIDATE");
  const [text, setText] = useState(""),
    [speaker, setSpeaker] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [reason, setReason] = useState("");
  const [confirmReason, setConfirmReason] = useState(""),
    [confirmAck, setConfirmAck] = useState(false);
  const [selection, setSelection] = useState<{
    start: number;
    end: number;
    key: number;
  } | null>(null);
  const scope = useRef(0),
    sequence = useRef(0),
    selectedRef = useRef(""),
    routePending = useRef(requestedRunId || "");
  const videos = data.assets.filter((a) => a.kind === "video");
  useEffect(() => {
    scope.current++;
    sequence.current++;
    selectedRef.current = "";
    routePending.current = requestedRunId || "";
    setEnabled(null);
    setItems([]);
    setSelected("");
    setSnapshot(null);
    setAssetId("");
    setActive(null);
    setSelection(null);
    setError("");
    setTrial(false);
    setFilter("all");setUnitFilter("");setQuery("");
    setConfirmReason("");
    setConfirmAck(false);
  }, [projectId, requestedRunId]);
  const reload = useCallback(async () => {
    if (selected !== selectedRef.current) return;
    const token = scope.current;
    const request = ++sequence.current;
    try {
      const list = await api<{ enabled: boolean; items: Snapshot[] }>(
        `/projects/${projectId}/video-contents`,
      );
      if (token !== scope.current || request !== sequence.current) return;
      setItems(list.items);
      setEnabled(list.enabled);
      const requested = requestedRunId && routePending.current
        ? list.items.find((item) => (item.run_id === routePending.current || item.id === routePending.current))
        : undefined;
      routePending.current = "";
      const chosen =
        requested?.id ||
        selected ||
        localStorage.getItem(`content-snapshot:${projectId}`) ||
        list.items[0]?.id ||
        "";
      const valid = list.items.some((s) => s.id === chosen)
        ? chosen
        : list.items[0]?.id || "";
      if (valid !== selected) {
        selectedRef.current = valid;
        setSelected(valid);
        return;
      }
      if (valid) {
        const detail = await api<Snapshot>(`/video-contents/${valid}`);
        if (token === scope.current && request === sequence.current)
          setSnapshot(detail);
      }
      setError("");
    } catch (e) {
      if (token === scope.current && request === sequence.current)
        setError((e as Error).message);
    }
  }, [projectId, selected, requestedRunId]);
  const invalidate = useCallback(() => {
    sequence.current++;
  }, []);
  useEffect(() => {
    void reload();
    return invalidate;
  }, [reload, invalidate]);
  useEffect(() => {
    if (!snapshot || !["QUEUED", "RUNNING"].includes(snapshot.run_status || ""))
      return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [snapshot, reload]);
  function choose(id: string) {
    routePending.current = "";
    if (id === selectedRef.current) return;
    sequence.current++;
    selectedRef.current = id;
    setSelected(id);
    localStorage.setItem(`content-snapshot:${projectId}`, id);
    setSnapshot(null);
    setActive(null);
    setSelection(null);
    setEdit(false);
    setOffset(0);
    setFilter("all");setUnitFilter("");setQuery("");
    setUnknownAck(false);
    setConfirmAck(false);
    setConfirmReason("");
  }
  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    const token = scope.current;
    try {
      await act(work);
    } catch (e) {
      if (token === scope.current) setError((e as Error).message);
    } finally {
      if (token === scope.current) setBusy(false);
    }
  }
  const create = () =>
    action(async () => {
      const result = await post<{ id: string }>(
        `/projects/${projectId}/video-contents`,
        {
          asset_id: assetId,
          idempotency_key: jobKey(),
          acknowledge_trial: trial,
        },
      );
      choose(result.id);
    });
  function locate(fact: Fact) {
    setActive(fact);
    setEdit(false);
    setReason("");
    setSelection({
      start: Math.max(0, fact.start_ms - 300),
      end: Math.min(
        snapshot?.payload.duration_ms || fact.end_ms,
        fact.end_ms + 300,
      ),
      key: Date.now(),
    });
  }
  function editFact() {
    if (!active) return;
    setEditStatus(active.status);
    setText(active.raw_content);
    setSpeaker(active.speaker_id || "");
    setStart(String(active.start_ms));
    setEnd(String(active.end_ms));
    setEdit(true);
  }
  const save = () =>
    action(async () => {
      if (!snapshot || !active) return;
      const result = await post<Snapshot>(
        `/video-contents/${snapshot.id}/revisions`,
        {
          expected_content_hash: snapshot.content_hash,
          reason,
          corrections: [
            {
              event_id: active.id,
              raw_content: text,
              start_ms: Number(start),
              end_ms: Number(end),
              speaker_id: speaker || null,
              status: editStatus,
            },
          ],
        },
      );
      choose(result.id);
    });
  const events = (snapshot?.payload.events || [])
    .filter(
      (e) =>
        (!unitFilter || e.unit_ids.includes(unitFilter)) &&
        (filter === "all" ||
          (filter === "unclassified_audio"
            ? e.origin.audio_classification === "UNKNOWN"
            : e.channel === filter &&
              e.origin.audio_classification !== "UNKNOWN")) &&
        (!query ||
          `${e.raw_content} ${e.speaker_id || ""} ${e.id}`
            .toLowerCase()
            .includes(query.toLowerCase())),
    )
    .sort((a, b) => a.start_ms - b.start_ms || a.id.localeCompare(b.id));
  const visibleGaps = [...(snapshot?.payload.gaps||[]),...(snapshot?.additional_checks?.gaps||[])];
  function replayRange(start:number,end:number){setActive(null);setEdit(false);setSelection({start:Math.max(0,start),end:Math.min(snapshot?.payload.duration_ms||end,end),key:Date.now()});if(window.matchMedia('(max-width:800px)').matches)document.querySelector('.content-player')?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:'start'});}
  const asrCaps =
    snapshot?.payload.asr_capabilities || snapshot?.asr_capabilities;
  const original = data.assets.find(
    (a) => a.id === (snapshot?.asset_id || assetId),
  );
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">VIDEO CONTENT / EVIDENCE</div>
          <h1>视频审阅</h1>
          <p>先处理影响交付的问题，必要时再展开原始证据。</p>
        </div>
        <button onClick={() => void reload()} disabled={busy}>
          <RefreshCw size={15} />
          刷新内容状态
        </button>
      </div>
      {snapshot?.superseded_by && (
        <div className="notice compact">
          此版本已有修正；原始候选仍可查看，请选择最新内容版本继续修正或审核。
        </div>
      )}
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <details className="content-setup"><summary>上传或重新读取视频</summary><section className="panel content-create">
        <label>
          选择完整原视频
          <select
            aria-label="选择内容读取原视频"
            value={assetId}
            onChange={(e) => setAssetId(e.target.value)}
          >
            <option value="">请选择原片</option>
            {videos.map((a) => (
              <option key={a.id} value={a.id}>
                {a.filename} · {time(a.metadata.duration_ms || 0)}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={trial}
            onChange={(e) => setTrial(e.target.checked)}
          />
          确认仅作技术试审；音画与内容候选需回放核对，不代表品牌批准
        </label>
        <button
          className="primary"
          disabled={busy || !enabled || !assetId || !trial}
          onClick={() => void create()}
        >
          <ScanLine size={16} />
          读取完整视频内容
        </button>
        {enabled === false && <p className="muted">内容读取预览未启用。</p>}
        {enabled === null && <p className="small muted">正在读取配置…</p>}
      </section>
      </details><div className="content-version">
        <label>
          内容版本
          <select
            aria-label="选择内容快照"
            value={selected}
            onChange={(e) => choose(e.target.value)}
          >
            <option value="">
              {items.length ? "请选择内容版本" : "暂无内容快照"}
            </option>
            {items.map((s) => (
              <option key={s.id} value={s.id}>
                {sourceLabel(s)} · {s.parent_id ? "修正版" : "原始读取"} ·{" "}
                {statusText[s.run_status || s.status] || s.status} ·{" "}
                {s.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        {snapshot?.content_hash && (
          <a
            className="button"
            href={`/api/v1/video-contents/${snapshot.id}/archive`}
            download
          >
            <Download size={15} />
            导出内容 JSON
          </a>
        )}
      </div>
      {!snapshot && (
        <div className="panel empty-content">
          选择原片建立内容时间轴；声音、字幕、动作和产品身份分别保留证据。
        </div>
      )}
      {snapshot && (
        <>
          <section className="panel content-summary">
            <div>
              <strong>
                {statusText[
                  ["INCOMPLETE", "CANCELED"].includes(snapshot.run_status || "")
                    ? snapshot.run_status || ""
                    : snapshot.status
                ] || snapshot.status}
              </strong>
              <span>
                SHA256 {snapshot.source_sha256.slice(0, 16)}… · 内容版本{" "}
                {snapshot.id.slice(0, 8)}
              </span>
            </div>
            <details><summary>识别记录与处理详情</summary><div className="content-counts">
              <span>
                主单元 <b>{snapshot.report.processing_complete ? snapshot.report.unit_count || 0 : "待汇总"}</b>
              </span>
              <span>
                事实候选 <b>{snapshot.report.processing_complete ? snapshot.report.event_count || 0 : "待汇总"}</b>
              </span>
              <span>
                实体候选 <b>{snapshot.report.processing_complete ? snapshot.report.entity_count || 0 : "待汇总"}</b>
              </span>
              <span>
                解释/关系 <b>{snapshot.report.processing_complete ? snapshot.report.claim_count || 0 : "待汇总"}</b>
              </span>
              <span>
                待核对 <b>{snapshot.report.processing_complete ? visibleGaps.length : "待汇总"}</b>
              </span>
            </div>
            <p>
              区间覆盖和四轮处理完成不等于内容准确。所有模型候选与人工修正保留版本；未获生产资格。
            </p>
            {snapshot.error && (
              <div role="alert" className="error">
                {snapshot.error.message}（{snapshot.error.code}）
              </div>
            )}
            {snapshot.run_status === "INCOMPLETE" && (
              <div>
                {snapshot.error?.unknown_remote_state && (
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={unknownAck}
                      onChange={(e) => setUnknownAck(e.target.checked)}
                    />
                    已核对远端状态，了解重试可能重复调用
                  </label>
                )}
                <button
                  disabled={
                    busy ||
                    (!!snapshot.error?.unknown_remote_state && !unknownAck)
                  }
                  onClick={() =>
                    void action(async () => {
                      await post(`/runs/${snapshot.run_id}/retry`, {
                        expected_revision: (
                          await api<{ revision: number }>(
                            `/runs/${snapshot.run_id}`,
                          )
                        ).revision,
                        acknowledge_unknown_remote: unknownAck,
                      });
                      await reload();
                    })
                  }
                >
                  恢复内容读取
                </button>
              </div>
            )}
            <details open={!snapshot.content_hash}>
              <summary>读取阶段与能力</summary>
              <p className="small">
                逐词记录 {asrCaps?.with_words ?? "尚无结果"} / 说话人分句{" "}
                {asrCaps?.with_speaker ?? "尚无结果"}；声音编号不等于画中人物。
              </p>
              <ol className="content-stages">
                {snapshot.stages.map((s, i) => (
                  <li key={`${s.name}-${s.attempt}-${i}`}>
                    <span>
                      {s.name} · 尝试 {s.attempt}
                    </span>
                    <b>{statusText[s.status] || s.status}</b>
                    {s.error && (
                      <span className="error">{s.error.message}</span>
                    )}
                  </li>
                ))}
              </ol>
            </details>
            </details>
          </section>
          <div className="content-workspace">
            <aside className="content-player">
              {original && (
                <VideoPlayer
                  assetId={original.id}
                  duration={
                    snapshot.payload.duration_ms ||
                    original.metadata.duration_ms ||
                    0
                  }
                  selection={selection}
                />
              )}
              {active && (
                <section className="panel content-detail">
                  <h2>当前事实候选</h2>
                  <p>
                    {active.id} ·{" "}
                    {
                      channels[
                        active.origin.audio_classification === "UNKNOWN"
                          ? "unclassified_audio"
                          : active.channel
                      ]
                    }{" "}
                    · {time(active.start_ms)}–{time(active.end_ms)}
                  </p>
                  <blockquote>{active.raw_content}</blockquote>
                  <p className="small">
                    来源 {active.origin.stage} · 说话人{" "}
                    {active.speaker_id || "未知"} · 主体{" "}
                    {active.actor_id || "未知"} · 物品{" "}
                    {active.object_id || "未知"}
                  </p>
                  <p className="small">
                    匿名主体、声音与产品层级均需证据；未知不补全。
                  </p>
                  <button
                    onClick={editFact}
                    disabled={!!snapshot.superseded_by}
                  >
                    修正当前内容候选
                  </button>
                  {edit && (
                    <div className="content-edit">
                      <label>
                        原文/内容
                        <textarea
                          aria-label="修正内容文字"
                          value={text}
                          onChange={(e) => setText(e.target.value)}
                        />
                      </label>
                      <label>
                        辨认状态
                        <select
                          aria-label="内容辨认状态"
                          value={editStatus}
                          onChange={(e) => setEditStatus(e.target.value)}
                        >
                          <option value="CANDIDATE">候选内容</option>
                          <option value="UNCLEAR">辨认不清，需重新核对</option>
                          <option value="NOT_OBSERVED">
                            本范围未观察到，不推为全片没有
                          </option>
                        </select>
                      </label>
                      <label>
                        匿名说话人
                        <input
                          value={speaker}
                          onChange={(e) => setSpeaker(e.target.value)}
                        />
                      </label>
                      <label>
                        起点（毫秒）
                        <input
                          type="number"
                          value={start}
                          onChange={(e) => setStart(e.target.value)}
                        />
                      </label>
                      <label>
                        终点（毫秒）
                        <input
                          type="number"
                          value={end}
                          onChange={(e) => setEnd(e.target.value)}
                        />
                      </label>
                      <label>
                        修正依据
                        <textarea
                          aria-label="内容修正依据"
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                        />
                      </label>
                      <p className="small">
                        保存为新内容版本；受影响解释标为需复核，原始候选保留，引用旧内容的合规审核将标为过期。
                      </p>
                      <button
                        disabled={busy || !text.trim() || !reason.trim()}
                        onClick={() => void save()}
                      >
                        保存为新内容版本
                      </button>
                      <button onClick={() => setEdit(false)}>取消修正</button>
                    </div>
                  )}
                </section>
              )}
            </aside>
            <div className="decision-column">
              {snapshot.content_hash&&<VisualInspectionPanel key={snapshot.id} snapshotId={snapshot.id} assetId={snapshot.asset_id} hash={snapshot.content_hash} superseded={!!snapshot.superseded_by} onCreated={()=>void act(async()=>{})} onLocate={replayRange}/>}
              <div className="raw-toggle"><button aria-expanded={showRaw} onClick={()=>setShowRaw(!showRaw)}>{showRaw?'收起原始记录':`查看原始识别与对账记录（${snapshot.report.event_count||0} 条）`}</button><span>用于追溯，不是待修改问题清单</span></div>
            {showRaw&&<section className="panel content-timeline">
              <div className="tabs">
                {[
                  ["events", "事实时间轴"],
                  ["entities", "实体身份"],
                  ["claims", "解释与关系"],
                  ["gaps", "待核对"],
                ].map(([id, name]) => (
                  <button
                    key={id}
                    className={tab === id ? "active" : ""}
                    onClick={() => {
                      setTab(id);
                      setOffset(0);
                    }}
                  >
                    {name}
                  </button>
                ))}
              </div>
              {tab === "events" && (
                <>
                  <div className="content-filters">
                    <select
                      aria-label="内容通道"
                      value={filter}
                      onChange={(e) => {
                        setFilter(e.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="all">所有通道</option>
                      {Object.entries(channels).map(([id, name]) => (
                        <option key={id} value={id}>
                          {name}
                        </option>
                      ))}
                    </select>
                    <select aria-label="主单元范围" value={unitFilter} onChange={e=>{setUnitFilter(e.target.value);setOffset(0);setActive(null);setEdit(false);}}>
                      <option value="">所有主单元</option>
                      {(snapshot.payload.units||[]).map(unit=><option key={unit.id} value={unit.id}>{unit.id} · {time(unit.start_ms)}–{time(unit.end_ms)} · {unit.boundary_reason}</option>)}
                    </select>
                    <input
                      aria-label="搜索内容事实"
                      placeholder="搜索原文、说话人或事件 ID"
                      value={query}
                      onChange={(e) => {
                        setQuery(e.target.value);
                        setOffset(0);
                      }}
                    />
                  </div>
                  <p className="small muted">
                    {events.length}条候选 · 每页30条 ·
                    来源路径保持独立，不将相同表述自动合为事实
                  </p>
                  {events.slice(offset, offset + 30).map((e) => (
                    <button
                      key={e.id}
                      className={`content-event ${active?.id === e.id ? "selected" : ""}`}
                      onClick={() => locate(e)}
                    >
                      <span className="event-time">
                        {time(e.start_ms)}–{time(e.end_ms)}
                        <small>
                          {
                            channels[
                              e.origin.audio_classification === "UNKNOWN"
                                ? "unclassified_audio"
                                : e.channel
                            ]
                          }{" "}
                          · {e.origin.stage}
                        </small>
                      </span>
                      <span>
                        {e.raw_content}
                        <small>
                          {e.speaker_id || "说话人未知"} ·{" "}
                          {statusText[e.status] || e.status}
                        </small>
                      </span>
                    </button>
                  ))}
                  {!events.length && (
                    <p className="content-empty">
                      暂无匹配事实候选；处理中或未识别都不能当成不存在。
                    </p>
                  )}
                  <div className="actions">
                    <button
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 30))}
                    >
                      上一组事实
                    </button>
                    <button
                      disabled={offset + 30 >= events.length}
                      onClick={() => setOffset(offset + 30)}
                    >
                      下一组事实
                    </button>
                  </div>
                </>
              )}
              {tab === "entities" && (
                <div className="content-list">
                  {snapshot.payload.entities?.map((e) => (
                    <article key={e.id}>
                      <strong>{e.description}</strong>
                      <p>
                        {e.id} · {e.identity_level} · 依据 {e.basis} ·{" "}
                        {statusText[e.status] || e.status}
                      </p>
                      <p className="small">
                        品牌杯、口播自称和SKU身份分别举证，不自动确认。
                      </p>
                      <div className="actions">
                        {e.evidence_ids.map((id) => (
                          <button
                            key={id}
                            onClick={() => {
                              const fact = snapshot.payload.events?.find(
                                (x) => x.id === id,
                              );
                              if (fact) locate(fact);
                            }}
                          >
                            {id}
                          </button>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {tab === "claims" && (
                <div className="content-list">
                  {snapshot.payload.claims?.map((c) => (
                    <article key={c.id}>
                      <strong>{c.description}</strong>
                      <p>
                        {c.kind} · {c.relation} ·{" "}
                        {statusText[c.status] || c.status}
                        {c.requires_recheck ? " · 修正后需重新核对" : ""}
                      </p>
                      <div className="actions">
                        {c.evidence_ids.map((id) => (
                          <button
                            key={id}
                            onClick={() => {
                              const fact = snapshot.payload.events?.find(
                                (x) => x.id === id,
                              );
                              if (fact) locate(fact);
                            }}
                          >
                            {id}
                          </button>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {tab === "gaps" && (
                <div className="content-list">
                  <p className="small">原始快照 {snapshot.payload.gaps?.length||0} 项；当前词时间检查 {snapshot.additional_checks?.gaps.length||0} 项（{snapshot.additional_checks?.status||"等待读取"}）。这是识别分歧与限制，不等于违规。</p>
                  {visibleGaps.map((g, i) => {
                    const ids=[...new Set([g.event_id,...(g.candidate_ids||[]),...(g.event_ids||[])].filter((id):id is string=>!!id))].filter(id=>snapshot.payload.events?.some(event=>event.id===id));
                    const unit=snapshot.payload.units?.find(item=>item.id===g.unit_id);
                    return <article key={i}>
                      <strong>{g.code==="VIDEO_SPEECH_WITHOUT_ASR_WORDS"?"口播候选缺少对应ASR词时间":g.code==="UNVERIFIED"?"待回放核对":g.code==="SCOPE_VIOLATION"?"片段主张越界，未获证实":g.code}</strong>
                      <p>{g.reason}</p>
                      <div className="actions">
                        {ids.map(id=><button key={id} onClick={()=>{const fact=snapshot.payload.events?.find(event=>event.id===id);if(fact)locate(fact);}}>查看 {id}</button>)}
                        {!ids.length&&g.range_ms?.length===2&&<button onClick={()=>replayRange(g.range_ms![0],g.range_ms![1])}>回放 {time(g.range_ms[0])}–{time(g.range_ms[1])}</button>}
                        {!ids.length&&!g.range_ms&&unit&&<button onClick={()=>replayRange(unit.start_ms,unit.end_ms)}>回放 {unit.id}</button>}
                        {!ids.length&&!g.range_ms&&!unit&&<button onClick={()=>replayRange(0,snapshot.payload.duration_ms||0)}>回放完整原片</button>}
                      </div>
                    </article>;
                  })}
                </div>
              )}
            </section>}
            </div>
          </div>
          {snapshot.content_hash && (
            <details className="panel content-confirm"><summary>人工回放确认（完成核对后使用）</summary>
              <h2>内容回放确认</h2>
              <p>这是对内容版本的回放记录，独立于品牌合规批准和人工金标准。</p>
              {snapshot.confirmation ? (
                <p>已记录：{snapshot.confirmation.reason}</p>
              ) : (
                <>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={confirmAck}
                      onChange={(e) => setConfirmAck(e.target.checked)}
                    />
                    我已回放本版本，了解仍有未决候选与识别限制
                  </label>
                  <label>
                    确认说明
                    <textarea
                      aria-label="内容回放确认说明"
                      value={confirmReason}
                      onChange={(e) => setConfirmReason(e.target.value)}
                    />
                  </label>
                  <button
                    disabled={busy || !confirmAck || !confirmReason.trim()}
                    onClick={() =>
                      void action(async () => {
                        await post(
                          `/video-contents/${snapshot.id}/confirmation`,
                          {
                            expected_content_hash: snapshot.content_hash,
                            expected_checks_hash: snapshot.additional_checks?.hash,
                            reason: confirmReason,
                            acknowledge_remaining_gaps: confirmAck,
                          },
                        );
                        await reload();
                      })
                    }
                  >
                    记录内容回放确认
                  </button>
                </>
              )}
              <p className="small">
                此版本包含 {snapshot.correction_log.length}{" "}
                项修正记录；不会改写历史视频审核。
              </p>
            </details>
          )}
        </>
      )}
    </>
  );
}
