import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  Download,
  FileSearch,
  ListChecks,
  Play,
  RefreshCw,
  StopCircle,
  UserCheck,
} from "lucide-react";
import { api, post } from "../api/client";
import type { Block, Evidence, Run, WorkspaceProps } from "../api/types";
import {
  Badge,
  Empty,
  Loading,
  Modal,
  date,
  label,
  time,
  awaitsAudioVerification,
} from "../components/Common";
import { VideoPlayer } from "../components/VideoPlayer";

function stageLabel(name: string) {
  const prefixes: Record<string, string> = {
    "inspect:": "连续视频精查",
    "prepare:": "准备连续片段",
    "verify:": "证据复核",
    "verify_clip:": "准备复核区间",
    "validate_timeline:": "校验片段源时间映射",
    "verify_timeline:": "校验复核区间时间映射",
    "extract:": "逐页规范提取",
  };
  for (const [prefix, title] of Object.entries(prefixes)) {
    if (name.startsWith(prefix)) return title;
  }
  const stages: Record<string, string> = {
    media_validate: "原片验证",
    observe_full: "整片中立观察",
    extract_audio: "完整音轨提取",
    transcribe_audio: "独立 ASR",
    evidence_verification: "证据复核",
    evaluate_rules: "逐规则判定",
    validate_evidence_references: "证据引用校验",
    auxiliary_materials: "封面与文案",
    review_script: "脚本逐条审核",
    resolve_rules: "规范关系与冲突",
    resolve_rule_references: "规范关系引用校验",
    parse: "文件解析",
  };
  return stages[name] || name;
}
export default function ReviewPage(props: WorkspaceProps & { runId: string }) {
  const { data, config, busy, act, onRun, onAsset, runId } = props;
  const [run, setRun] = useState<Run | null>(null),
    [error, setError] = useState(""),
    [filter, setFilter] = useState("all"),
    [evidenceQuery, setEvidenceQuery] = useState(""),
    [evidenceChannel, setEvidenceChannel] = useState("all"),
    [selection, setSelection] = useState<{
      start: number;
      end: number;
      key: number;
    } | null>(null),
    [selectedEvidence, setSelectedEvidence] = useState(""),
    [decision, setDecision] = useState(false),
    [reason, setReason] = useState(""),
    [decisionType, setDecisionType] = useState("APPROVE"),
    [retryConfirm, setRetryConfirm] = useState(false),
    [mode, setMode] = useState("ai_assisted"),
    [selectedVideo, setSelectedVideo] = useState("");
  const requestSequence = useRef(0);
  const [timelineChoices, setTimelineChoices] = useState<Evidence[] | null>(
    null,
  );
  const invalidateRequest = useCallback(() => {
    requestSequence.current++;
  }, []);
  const reload = useCallback(async () => {
    if (!runId) return;
    const sequence = ++requestSequence.current;
    try {
      const result = await api<Run>(`/runs/${runId}`);
      if (sequence !== requestSequence.current) return;
      if (result.project_id !== data.project.id)
        throw new Error("审核记录不属于当前项目，请重新选择记录。");
      setRun(result);
      setError("");
    } catch (e) {
      if (sequence === requestSequence.current) setError((e as Error).message);
    }
  }, [runId, data.project.id]);
  const resume = (acknowledge: boolean) =>
    act(async () => {
      if (!run) return;
      await post(`/runs/${run.id}/retry`, {
        expected_revision: run.revision,
        acknowledge_unknown_remote: acknowledge,
      });
      setRetryConfirm(false);
      await reload();
    }, "正在恢复未完成阶段");
  useEffect(() => {
    setRun(null);
    setError("");
    setSelection(null);
    setTimelineChoices(null);
    setEvidenceQuery("");
    setEvidenceChannel("all");
    setSelectedEvidence("");
    void reload();
    return invalidateRequest;
  }, [reload, invalidateRequest]);
  useEffect(() => {
    if (!run || !["QUEUED", "RUNNING"].includes(run.status)) return;
    const interval = setInterval(() => void reload(), 2500);
    return () => clearInterval(interval);
  }, [run, reload]);
  const runVideo =
    run?.kind === "script"
      ? undefined
      : run?.assets.find((a) => a.kind === "video");
  const runSubmission = data.submissions.find(
    (s) => s.id === run?.submission_id,
  );
  const primaryAsset = run?.assets.find((a) => a.kind === run.kind);
  const standaloneVideo =
    data.assets.find((a) => a.id === selectedVideo) ||
    data.assets.find((a) => a.kind === "video");
  const video = runVideo || (!run ? standaloneVideo : undefined);
  const selectEvidence = (e: Evidence) => {
    setSelectedEvidence(e.id);
    if (e.start_ms !== null && e.end_ms !== null)
      setSelection({
        start: Math.max(0, e.start_ms - 500),
        end: e.end_ms + 500,
        key: Date.now(),
      });
    else {
      const asset = data.assets.find((a) => a.id === e.asset_id);
      if (asset) onAsset(asset, e.source_block_id || undefined);
    }
  };
  async function openRuleSource(ruleId: string) {
    const ref = run?.rules.find((r) => r.id === ruleId)?.source_refs[0];
    if (!ref) return;
    for (const a of data.assets.filter((a) => a.kind === "brief")) {
      const d = await api<{ blocks: Block[] }>(`/assets/${a.id}/blocks`);
      if (d.blocks.some((b) => b.id === ref.block_id)) {
        onAsset(a, ref.block_id);
        return;
      }
    }
  }
  if (error)
    return (
      <div className="notice danger">
        {error} <button onClick={() => onRun("")}>返回项目记录</button>
      </div>
    );
  if (runId && !run) return <Loading />;
  const shownChecks =
    run?.checks.filter((c) => filter === "all" || c.status === filter) || [];
  const isIndependent = run?.input_snapshot.mode === "independent_human";
  const meaningful = run && ["script", "video"].includes(run.kind);
  const extractionSources =
    run?.kind === "extract"
      ? data.rulesets.find((ruleset) => ruleset.id === run.ruleset_id)
          ?.source_ids
      : undefined;
  const extractionTotal = data.assets
    .filter((asset) => extractionSources?.includes(asset.id))
    .reduce((total, asset) => total + asset.coverage.length, 0);
  const extractedUnits = new Set(
    run?.stages
      .filter(
        (stage) =>
          stage.name.startsWith("extract:") && stage.status === "COMPLETED",
      )
      .map((stage) => stage.name),
  ).size;
  const timedEvidence =
    run?.evidence.filter((item) => item.start_ms !== null) || [];
  const matchingEvidence = timedEvidence.filter(
    (item) =>
      (evidenceChannel === "all" || item.channel === evidenceChannel) &&
      item.content
        .toLocaleLowerCase()
        .includes(evidenceQuery.trim().toLocaleLowerCase()),
  );
  const hasTrialContext =
    run?.input_snapshot.historical_trial ||
    !!run?.input_snapshot.trial_limits?.length;
  return (
    <section className="review-workspace" aria-label="审核工作台">
      <div className="page-title">
        <div>
          <div className="eyebrow">REVIEW WORKBENCH</div>
          <h1>
            {run?.kind === "script"
              ? "脚本审核"
              : run?.kind === "extract"
                ? "规范提取任务"
                : run?.kind === "parse"
                  ? "素材解析任务"
                  : "成片审核工作台"}
          </h1>
          <p className="review-record-meta">
            {run ? (
              <>
                {runSubmission?.creator && (
                  <strong>{runSubmission.creator}</strong>
                )}
                <span title={primaryAsset?.filename}>
                  {primaryAsset?.filename || label(run.kind)}
                </span>
                <span>
                  记录 {run.id.slice(0, 8)} · {date(run.created_at)}
                </span>
                {isIndependent && <span>人工独立审核，未执行 AI</span>}
              </>
            ) : (
              "观看完整原片，后续审核证据将直接定位到原片时间轴。"
            )}
          </p>
        </div>
        <div className="actions">
          {run && (
            <>
              <button onClick={() => void reload()}>
                <RefreshCw size={15} />
                刷新
              </button>
              <a
                className="button quiet-primary"
                href={`/api/v1/runs/${run.id}/report`}
                target="_blank"
                rel="noreferrer"
              >
                <Download size={15} />
                导出报告
              </a>
            </>
          )}
        </div>
      </div>
      <div className="workbench-select">
        <label>
          审核记录
          <select value={runId} onChange={(e) => onRun(e.target.value)}>
            <option value="">只看原始视频（未执行审核）</option>
            {data.runs.map((r) => {
              const submission = data.submissions.find(
                (s) => s.id === r.submission_id,
              );
              const original =
                r.kind === "video"
                  ? data.assets.find((a) => a.id === submission?.video_id)
                  : undefined;
              return (
                <option value={r.id} key={r.id}>
                  {label(r.kind)} ·{" "}
                  {submission ? `${submission.creator} · ` : ""}
                  {original?.metadata.duration_ms
                    ? `${time(original.metadata.duration_ms)} · `
                    : ""}
                  {r.id.slice(0, 8)} ·{" "}
                  {label(r.id === run?.id ? run.status : r.status)}
                </option>
              );
            })}
          </select>
        </label>
        {!run && (
          <label>
            原始视频
            <select
              value={video?.id || ""}
              onChange={(e) => setSelectedVideo(e.target.value)}
            >
              {data.assets
                .filter((a) => a.kind === "video")
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.metadata.duration_ms
                      ? `${time(a.metadata.duration_ms)} · `
                      : ""}
                    {a.filename}
                  </option>
                ))}
            </select>
          </label>
        )}
        {run && (
          <div className="actions">
            <Badge value={run.status} />
            {!meaningful && <Badge value={run.assessment} />}
            {run.ruleset_confirmation_kind && (
              <Badge value="confirmed">
                {run.ruleset_confirmation_kind === "brand_confirmed"
                  ? "品牌确认规范"
                  : "技术试验规范"}
              </Badge>
            )}
            {run.freshness === "STALE" && <Badge value="STALE" />}
          </div>
        )}
      </div>
      {meaningful && (
        <div className="review-result-summary">
          <div>
            <span className="result-summary-label">
              {isIndependent ? "人工审核记录" : "AI 审核结果"}
            </span>
            <strong>{label(run.assessment)}</strong>
          </div>
          <p>
            {run.checks.length} 项检查 · {run.evidence.length} 条证据
          </p>
          <span className="human-review-state">
            {run.decisions.length
              ? `已记录 ${run.decisions.length} 次人工决定`
              : "尚未记录人工决定"}
          </span>
        </div>
      )}
      {hasTrialContext && run && (
        <details className="review-context">
          <summary>
            <span className="context-marker" aria-hidden="true">
              !
            </span>
            <strong>受限技术试审 · 不能自动通过</strong>
            <span className="context-toggle">查看范围与限制</span>
          </summary>
          <div className="context-content">
            {run.input_snapshot.historical_trial && (
              <p>
                历史成片试审：没有对应版本的拍摄稿批准，不能据此认定脚本执行一致。
              </p>
            )}
            {run.input_snapshot.trial_limits?.map((limit, index) => (
              <p key={index}>{limit}</p>
            ))}
            {run.input_snapshot.trial_note && (
              <p>{run.input_snapshot.trial_note}</p>
            )}
          </div>
        </details>
      )}
      {run?.kind === "video" &&
        !isIndependent &&
        (run.input_snapshot.verification_context_version || 0) < 3 && (
          <div className="notice compact">
            <strong>旧版复核上下文记录</strong>
            <p>
              此记录未使用新版品牌快照与产品事实复核。竞品相关解释需重新核对，不能将主推品牌当作第三方；历史结论与证据原样保留。
            </p>
          </div>
        )}
      {!run && (
        <div className="notice">
          {config.video.ready
            ? "选择已冻结规范和达人提交后，即可创建真实审核任务。"
            : awaitsAudioVerification(config.video)
              ? "视频服务已配置，音画能力待人工回放验收。当前可播放原片，尚无成片审核结论。"
              : "视频模型与独立 ASR 尚未配置。当前可播放原片，尚无 AI 结论与问题时间点。"}
        </div>
      )}
      {run?.error && (
        <div className="notice danger">
          <strong>{run.error.message}</strong>
          <span className="small">
            {run.error.code} · 该状态不会被当成审核通过。
          </span>
        </div>
      )}
      <div className={`review-layout ${!video ? "text-review" : ""}`}>
        <div className="review-left">
          {video ? (
            <div className="review-media">
              <VideoPlayer
                assetId={video.id}
                selection={selection}
                duration={video.metadata.duration_ms || 0}
              />
              <div className="timeline-panel">
                <div className="section-line">
                  <h3>证据时间轴</h3>
                  <span className="muted small">
                    {run?.evidence.filter((e) => e.start_ms !== null).length ||
                      0}{" "}
                    条时间证据
                  </span>
                </div>
                <div className="evidence-timeline">
                  {run?.result.coverage?.map((c, i) => (
                    <span
                      key={`c${i}`}
                      className="coverage-segment"
                      style={{
                        left: `${(c.start_ms / (video.metadata.duration_ms || 1)) * 100}%`,
                        width: `${((c.end_ms - c.start_ms) / (video.metadata.duration_ms || 1)) * 100}%`,
                      }}
                    />
                  ))}
                  {run?.evidence
                    .filter((e) => e.start_ms !== null)
                    .map((e) => (
                      <button
                        key={e.id}
                        title={`${time(e.start_ms)} ${e.content}`}
                        className={`timeline-mark ${e.channel}`}
                        style={{
                          left: `${((e.start_ms || 0) / (video.metadata.duration_ms || 1)) * 100}%`,
                        }}
                        onClick={(event) => {
                          const width =
                            event.currentTarget.parentElement?.clientWidth || 1;
                          const nearbyMs = Math.max(
                            1000,
                            ((video.metadata.duration_ms || 1) * 8) / width,
                          );
                          setTimelineChoices(
                            run.evidence.filter(
                              (item) =>
                                item.start_ms !== null &&
                                Math.abs(item.start_ms - (e.start_ms || 0)) <=
                                  nearbyMs,
                            ),
                          );
                        }}
                      />
                    ))}
                </div>
                <div className="timeline-labels">
                  <span>00:00</span>
                  <span>{time(video.metadata.duration_ms || 0)}</span>
                </div>
                <div className="timeline-legend" aria-label="时间轴颜色说明">
                  <span className="legend-audio">口播</span>
                  <span className="legend-text">字幕</span>
                  <span className="legend-visual">画面与其他观察</span>
                </div>
                <p className="muted small">
                  绿色区间表示已处理时间范围，不能据此推断全部帧无遗漏。
                </p>
              </div>
            </div>
          ) : (
            <div className="panel script-evidence">
              <div className="panel-heading">
                <h2>
                  <FileSearch size={18} />
                  原文与观察证据
                </h2>
                {run?.assets.some((a) => a.kind === "script") && (
                  <button
                    className="text-button small"
                    onClick={() => {
                      const source = data.assets.find(
                        (a) =>
                          a.id ===
                          run.assets.find((a) => a.kind === "script")?.id,
                      );
                      if (source) onAsset(source);
                    }}
                  >
                    查看完整脚本 ↗
                  </button>
                )}
              </div>
              {run?.evidence.length ? (
                run.evidence.map((e) => (
                  <button
                    key={e.id}
                    className={`source-block ${e.id === selectedEvidence ? "selected" : ""}`}
                    onClick={() => selectEvidence(e)}
                  >
                    <span className="source-locator">
                      {label(e.channel)} · {e.origin.source}
                    </span>
                    <span className="source-content">{e.content}</span>
                    <span className="small active-link">
                      查看来源 <ChevronRight size={12} />
                    </span>
                  </button>
                ))
              ) : (
                <Empty
                  title="尚无审核证据"
                  description={
                    isIndependent
                      ? "此记录用于人工独立判断，没有模型输出。"
                      : "阶段执行后将显示可回源的真实证据。"
                  }
                />
              )}
            </div>
          )}
          {run && (
            <details className="panel stages-panel">
              <summary className="panel-heading">
                <h2>执行阶段</h2>
                <span className="muted small">
                  {extractionTotal > 0 ? (
                    <>
                      {extractedUnits} / {extractionTotal} 页 / 表已提取
                    </>
                  ) : (
                    <>
                      {
                        run.stages.filter((s) => s.status === "COMPLETED")
                          .length
                      }{" "}
                      / {run.stages.length} 条执行记录已完成
                    </>
                  )}
                </span>
              </summary>
              {run.stages.length ? (
                run.stages.map((s) => (
                  <div className="stage-row" key={s.id}>
                    <span className={`stage-dot ${s.status.toLowerCase()}`}>
                      {s.status === "COMPLETED" && <Check size={10} />}
                    </span>
                    <div>
                      <strong>{stageLabel(s.name)}</strong>
                      <span className="small muted block">
                        {s.name} · 第 {s.attempt} 次
                        {s.error ? ` · ${s.error.message}` : ""}
                      </span>
                    </div>
                    <Badge value={s.status} />
                  </div>
                ))
              ) : (
                <div className="panel-pad muted">
                  {isIndependent
                    ? "人工独立记录未执行模型阶段。"
                    : "等待后台任务开始。"}
                </div>
              )}
              <div className="panel-pad actions">
                {["QUEUED", "RUNNING"].includes(run.status) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await post(`/runs/${run.id}/cancel`);
                        await reload();
                      }, "已请求停止后续阶段")
                    }
                  >
                    <StopCircle size={15} />
                    取消后续阶段
                  </button>
                )}
                {["FAILED", "INCOMPLETE", "CANCELED"].includes(run.status) &&
                  !isIndependent && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        run.error?.unknown_remote_state
                          ? setRetryConfirm(true)
                          : void resume(false)
                      }
                    >
                      <RefreshCw size={15} />
                      恢复未完成阶段
                    </button>
                  )}
              </div>
            </details>
          )}
        </div>
        <div className="review-right">
          <div className="panel findings-panel">
            <div className="panel-heading">
              <h2>
                <ListChecks size={18} />
                逐条审核
              </h2>
              <span className="count-bubble">{run?.checks.length || 0}</span>
            </div>
            <div className="tabs findings-tabs" aria-label="审核结果筛选">
              {["all", "FAIL", "UNCERTAIN", "PASS"].map((s) => (
                <button
                  key={s}
                  onClick={() => setFilter(s)}
                  aria-pressed={filter === s}
                  className={filter === s ? "active" : ""}
                >
                  {s === "all" ? "全部" : s === "FAIL" ? "修改候选" : label(s)}
                  <span>
                    {s === "all"
                      ? run?.checks.length || 0
                      : run?.checks.filter((check) => check.status === s)
                          .length || 0}
                  </span>
                </button>
              ))}
            </div>
            <div className="findings-results" aria-live="polite">
              {shownChecks.length ? (
                shownChecks.map((c) => {
                  const rule = run?.rules.find((r) => r.id === c.rule_id);
                  return (
                    <article
                      key={c.rule_id}
                      className={`finding-card ${c.status.toLowerCase()}`}
                    >
                      <div className="section-line">
                        <Badge value={c.status} />
                        <span className="small muted">
                          {label(rule?.type || "")} ·{" "}
                          {label(rule?.severity || "")}
                        </span>
                        <button
                          className="text-button small"
                          onClick={() =>
                            void act(() => openRuleSource(c.rule_id))
                          }
                        >
                          Brief 出处 ↗
                        </button>
                      </div>
                      <h3>{rule?.title || c.rule_id}</h3>
                      <p>{c.explanation}</p>
                      {c.evidence_ids.map((id) => {
                        const e = run?.evidence.find((e) => e.id === id);
                        return e ? (
                          <button
                            key={id}
                            className={`evidence-card ${selectedEvidence === id ? "selected" : ""}`}
                            onClick={() => selectEvidence(e)}
                          >
                            <span>
                              <Play size={12} />
                              {e.start_ms !== null
                                ? `${time(e.start_ms)} – ${time(e.end_ms)}`
                                : "原文定位"}{" "}
                              · {label(e.channel)}
                            </span>
                            <q>{e.content}</q>
                          </button>
                        ) : null;
                      })}
                      {c.suggestion && (
                        <div className="suggestion">
                          <strong>修改建议</strong>
                          <p>{c.suggestion}</p>
                        </div>
                      )}
                      {c.limitations.map((l, i) => (
                        <p key={i} className="small muted">
                          待核对：{l}
                        </p>
                      ))}
                    </article>
                  );
                })
              ) : (
                <Empty
                  title={run ? "暂时没有逐条结果" : "AI 尚未开始审核"}
                  description={
                    run?.status === "COMPLETED"
                      ? "当前筛选没有检查项。"
                      : "连接模型、确认规范并提交审核后，将显示具体问题与依据。"
                  }
                />
              )}
            </div>
          </div>
          {timedEvidence.length > 0 && (
            <details className="panel evidence-catalogue">
              <summary className="panel-heading">
                <h2>声音 / 字幕 / 画面</h2>
                <span className="count-bubble">{timedEvidence.length}</span>
              </summary>
              <div className="evidence-search">
                <label>
                  <span>搜索证据内容</span>
                  <input
                    type="search"
                    value={evidenceQuery}
                    onChange={(event) => setEvidenceQuery(event.target.value)}
                    placeholder="输入台词、产品名或画面描述"
                  />
                </label>
                <label>
                  <span>证据通道</span>
                  <select
                    value={evidenceChannel}
                    onChange={(event) => setEvidenceChannel(event.target.value)}
                  >
                    <option value="all">全部通道</option>
                    {Array.from(
                      new Set(timedEvidence.map((item) => item.channel)),
                    ).map((channel) => (
                      <option key={channel} value={channel}>
                        {label(channel)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="evidence-match-count" role="status">
                显示 {matchingEvidence.length} / {timedEvidence.length} 条证据
              </p>
              <div className="observations-list">
                {matchingEvidence.map((e) => (
                  <button
                    className={`observation-row ${selectedEvidence === e.id ? "selected" : ""}`}
                    key={e.id}
                    onClick={() => selectEvidence(e)}
                  >
                    <span className="small muted">
                      {time(e.start_ms)} · {label(e.channel)}
                    </span>
                    <p>{e.content}</p>
                  </button>
                ))}
                {!matchingEvidence.length && (
                  <Empty
                    title="没有匹配的证据"
                    description="尝试缩短关键词或切换证据通道。"
                  />
                )}
              </div>
            </details>
          )}
          {meaningful && (
            <div className="panel decision-panel">
              <div className="panel-heading">
                <h2>
                  <UserCheck size={18} />
                  人工确认
                </h2>
              </div>
              <div className="panel-pad">
                <p className="muted">
                  {isIndependent
                    ? "这是一份人工独立审核记录。通过不会改变 AI 尚未审核的事实。"
                    : "AI 建议与人工决定分别保存。人工覆盖问题时，必须记录依据。"}
                </p>
                <button
                  className="primary full"
                  disabled={
                    busy ||
                    run.freshness === "STALE" ||
                    ["QUEUED", "RUNNING"].includes(run.status)
                  }
                  onClick={() => {
                    setReason("");
                    setMode(
                      isIndependent || run.status !== "COMPLETED"
                        ? "independent_human"
                        : "ai_assisted",
                    );
                    setDecision(true);
                  }}
                >
                  记录人工决定
                </button>
                {run.kind === "script" && run.status === "COMPLETED" && (
                  <button
                    className="full"
                    disabled={busy || run.freshness === "STALE"}
                    onClick={() => {
                      setReason("");
                      setMode("script_approval");
                      setDecision(true);
                    }}
                  >
                    批准拍摄脚本
                  </button>
                )}
                {run.decisions.map((d) => (
                  <div className="decision-history" key={d.id}>
                    <Badge value={d.decision} />
                    <span className="small muted">
                      {d.actor} · {date(d.created_at)} ·{" "}
                      {d.mode === "independent_human"
                        ? "人工独立审核"
                        : "AI 辅助审核"}
                    </span>
                    <p>{d.reason}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
          {run?.result.limits && (
            <details className="review-context result-limits">
              <summary>
                <strong>模型观察与限制</strong>
                <span className="count-bubble">{run.result.limits.length}</span>
              </summary>
              <div className="context-content">
                {run.result.limits.map((l, i) => (
                  <p key={i}>{l}</p>
                ))}
              </div>
            </details>
          )}
        </div>
      </div>
      {retryConfirm && (
        <Modal
          title="核对上次远端调用状态"
          onClose={() => setRetryConfirm(false)}
        >
          <p className="muted">
            上次调用已发送但没有完整响应，服务端不能确定供应商是否已处理。恢复会复用已完成阶段，重发未完成阶段可能产生重复调用与计费。
          </p>
          <div className="form-actions">
            <button onClick={() => setRetryConfirm(false)}>
              先检查供应商记录
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void resume(true)}
            >
              确认恢复未完成阶段
            </button>
          </div>
        </Modal>
      )}
      {timelineChoices && (
        <Modal title="选择时间证据" onClose={() => setTimelineChoices(null)}>
          <p className="muted">
            相近标记可能重叠，请按来源与完整内容选择；定位区间保留该条证据的起止时间。
          </p>
          <div className="observations-list">
            {timelineChoices.map((item) => (
              <button
                key={item.id}
                className="observation-row"
                onClick={() => {
                  selectEvidence(item);
                  setTimelineChoices(null);
                }}
              >
                <span className="small muted">
                  {time(item.start_ms)} – {time(item.end_ms)} ·{" "}
                  {label(item.channel)} ·{" "}
                  {item.origin.source === "independent_asr"
                    ? "独立 ASR"
                    : stageLabel(item.origin.source || "")}
                </span>
                <p>{item.content}</p>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {decision && run && (
        <Modal
          title={mode === "script_approval" ? "批准拍摄稿" : "记录人工审核决定"}
          onClose={() => setDecision(false)}
        >
          <div className="form-stack">
            {mode !== "script_approval" && (
              <>
                <label>
                  决定
                  <select
                    value={decisionType}
                    onChange={(e) => setDecisionType(e.target.value)}
                  >
                    {["APPROVE", "REQUEST_CHANGES", "REJECT"].map((d) => (
                      <option value={d} key={d}>
                        {label(d)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  审核方式
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    {run.status === "COMPLETED" && !isIndependent && (
                      <option value="ai_assisted">
                        在完整 AI 审核基础上确认
                      </option>
                    )}
                    <option value="independent_human">
                      人工独立审核（不声明 AI 已通过）
                    </option>
                  </select>
                </label>
              </>
            )}
            <label>
              判断依据
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="写明认可、退回或覆盖 AI 问题的理由"
              />
            </label>
            <div className="notice compact">
              此决定绑定当前输入版本，后续更换稿件或规范会使旧结论过期。
            </div>
            <div className="form-actions">
              <button onClick={() => setDecision(false)}>取消</button>
              <button
                className="primary"
                disabled={busy || !reason.trim()}
                onClick={() =>
                  act(async () => {
                    if (mode === "script_approval")
                      await post("/script-approvals", {
                        run_id: run.id,
                        expected_input_hash: run.input_hash,
                        expected_revision: run.revision,
                        reason,
                      });
                    else
                      await post(`/runs/${run.id}/decisions`, {
                        expected_input_hash: run.input_hash,
                        expected_revision: run.revision,
                        decision: decisionType,
                        reason,
                        mode,
                      });
                    setDecision(false);
                    await reload();
                  }, "人工决定已追加保存")
                }
              >
                保存决定
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
