import { useCallback, useEffect, useState } from "react";
import { api, post, jobKey } from "../api/client";
import { time } from "../components/Common";
type Finding = {
  category: string;
  status: string;
  title: string;
  reason: string;
  suggestion: string;
  start_ms: number | null;
  end_ms: number | null;
  interval_recheck?: { support: string; observation: string };
};
type Run = {
  id: string;
  status: string;
  error?: { message: string; unknown_remote_state?: boolean };
  result: {
    method_version?: string;
    findings?: Finding[];
    limits?: string[];
    limitations?: string[];
  };
};
const names: Record<string, string> = {
  product_visibility: "产品展示",
  text_readability: "文字可读性",
  product_appearance: "颜色与外观",
  background_distraction: "背景干扰",
  effect_interference: "特效影响",
};
export default function VisualInspectionPanel({
  snapshotId,
  assetId,
  hash,
  superseded,
  onLocate,
  onCreated,
}: {
  snapshotId: string;
  assetId: string;
  hash: string;
  superseded: boolean;
  onCreated: () => void;
  onLocate: (start: number, end: number) => void;
}) {
  const [requestKey, setRequestKey] = useState(jobKey);
  const [runs, setRuns] = useState<Run[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [ack, setAck] = useState(false),
    [expanded, setExpanded] = useState(false);
  const load = useCallback(async () => {
    const value = await api<{ items: Run[] }>(
      `/video-contents/${snapshotId}/visual-inspections`,
    );
    setRuns(value.items);
  }, [snapshotId]);
  useEffect(() => {
    let current = true;
    api<{ items: Run[] }>(`/video-contents/${snapshotId}/visual-inspections`)
      .then((value) => {
        if (current) setRuns(value.items);
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
    };
  }, [snapshotId]);
  const latest = runs[0];
  const methodCurrent =
    latest?.result?.method_version === "visual-inspection-v3";
  const pending = latest && ["QUEUED", "RUNNING"].includes(latest.status);
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(
      () => void load().catch((e) => setError(e.message)),
      4000,
    );
    return () => clearInterval(timer);
  }, [pending, load]);
  async function start() {
    setBusy(true);
    setError("");
    try {
      await post(`/video-contents/${snapshotId}/visual-inspections`, {
        asset_id: assetId,
        expected_content_hash: hash,
        acknowledge_trial: true,
        idempotency_key: requestKey,
      });
      await load();
      setRequestKey(jobKey());
      onCreated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const findings = latest?.result.findings || [];
  const groups = Array.from(
    findings
      .reduce((map, f) => {
        const key = JSON.stringify([
          f.category,
          f.status,
          f.title,
          f.reason,
          f.suggestion,
        ]);
        const existing = map.get(key);
        if (existing) {
          if (
            f.start_ms !== null &&
            f.end_ms !== null &&
            !existing.ranges.some(
              (r) => r[0] === f.start_ms && r[1] === f.end_ms,
            )
          )
            existing.ranges.push([f.start_ms, f.end_ms]);
        } else
          map.set(key, {
            ...f,
            ranges:
              f.start_ms !== null && f.end_ms !== null
                ? [[f.start_ms, f.end_ms]]
                : [],
          });
        return map;
      }, new Map<string, Finding & { ranges: number[][] }>())
      .values(),
  );
  const actionable = groups
    .filter((f) => f.status === "ISSUE" || f.status === "UNCERTAIN")
    .sort(
      (a, b) => Number(a.status !== "ISSUE") - Number(b.status !== "ISSUE"),
    );
  return (
    <section className="visual-review panel">
      <div className="review-heading">
        <div>
          <span className="review-kicker">视频专项检查</span>
          <h2>
            {pending
              ? "正在查看完整视频"
              : latest?.status === "COMPLETED"
                ? !methodCurrent
                  ? "历史结果需要复核"
                  : actionable.length
                    ? `${actionable.length} 项需要关注`
                    : "本轮未发现明显问题"
                : "先看影响交付的问题"}
          </h2>
        </div>
        <button
          className="primary"
          disabled={
            busy ||
            !!pending ||
            superseded ||
            (!!latest?.error?.unknown_remote_state && !ack)
          }
          onClick={() => void start()}
        >
          {busy
            ? "正在提交…"
            : pending
              ? "检查进行中"
              : latest
                ? "重新检查"
                : "开始专项检查"}
        </button>
      </div>
      <p className="small">
        检查遮挡、文字、外观、背景与特效；这是修改建议，不是品牌批准。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {latest?.status === "INCOMPLETE" && (
        <p role="alert" className="error">
          检查未完成：{latest.error?.message || "请查看运行记录"}
          。不会据此显示通过。
        </p>
      )}
      {latest?.error?.unknown_remote_state && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
          />
          已核对远端任务，了解重新检查可能重复计费
        </label>
      )}
      {!latest && (
        <div className="review-empty">
          先运行一次检查，再按时间定位问题。原始内容已保留，不必逐条阅读全部识别记录。
        </div>
      )}
      {pending && (
        <div className="review-skeleton" role="status">
          完整原片已提交，结果返回前不作判断。
        </div>
      )}
      {latest?.status === "COMPLETED" && !methodCurrent && (
        <p className="error">
          历史结果尚未通过当前复核流程，请重新检查。历史原始响应保留在运行记录。
        </p>
      )}
      {latest?.status === "COMPLETED" && methodCurrent && (
        <>
          <div className="inspection-categories">
            {Object.entries(names).map(([key, name]) => {
              const group = findings.filter((f) => f.category === key);
              return (
                <span key={key}>
                  {name}
                  <b>
                    {group.some((f) => f.status === "ISSUE")
                      ? "疑似问题"
                      : group.some((f) => f.status === "UNCERTAIN")
                        ? "需核对"
                        : group.every((f) => f.status === "NOT_APPLICABLE")
                          ? "不适用"
                          : "未见明显问题"}
                  </b>
                </span>
              );
            })}
          </div>
          {(expanded ? groups : actionable.slice(0, 5)).map((f, index) => (
            <article className="inspection-finding" key={index}>
              <div>
                <span className="finding-category">{names[f.category]}</span>
                <strong>{f.title}</strong>
              </div>
              <p>
                <span className="muted">模型候选说明：</span>
                {f.reason}
              </p>
              {f.suggestion && (
                <p className="finding-suggestion">建议：{f.suggestion}</p>
              )}
              {f.interval_recheck && (
                <details>
                  <summary>查看区间复核观察</summary>
                  <p>
                    {f.interval_recheck.observation}（
                    {f.interval_recheck.support === "supports"
                      ? "复核支持候选，仍需人审"
                      : "复核未支持原判断"}
                    ）
                  </p>
                </details>
              )}
              {f.ranges.length > 0 && (
                <div className="evidence-ranges">
                  <span className="small muted">模型估计区间</span>
                  {f.ranges.map(([start, end]) => (
                    <button
                      key={`${start}-${end}`}
                      onClick={() => onLocate(start, end)}
                    >
                      回放 {time(start)}–{time(end)}
                    </button>
                  ))}
                </div>
              )}
            </article>
          ))}
          <button
            className="text-button"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "收起详细检查" : `展开全部 ${groups.length} 组检查`}
          </button>
          <details>
            <summary>本轮识别限制</summary>
            {[
              ...(latest.result.limitations || []),
              ...(latest.result.limits || []),
            ].map((value, index) => (
              <p key={index} className="small">
                {value}
              </p>
            ))}
          </details>
        </>
      )}
    </section>
  );
}
