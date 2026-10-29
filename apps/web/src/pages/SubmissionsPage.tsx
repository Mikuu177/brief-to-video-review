import { useEffect, useState } from "react";
import {
  Plus,
  GitBranch,
  ArrowUpRight,
  Sparkles,
  UserRound,
} from "lucide-react";
import { api, jobKey, post } from "../api/client";
import type { Submission, WorkspaceProps } from "../api/types";
import {
  Badge,
  Empty,
  FileIcon,
  Modal,
  date,
  label,
  awaitsAudioVerification,
} from "../components/Common";

export default function SubmissionsPage({
  data,
  config,
  busy,
  act,
  onRun,
  onAsset,
}: WorkspaceProps) {
  const [form, setForm] = useState<{
      creator: string;
      parent_id: string | null;
      script_id: string;
      video_id: string;
      cover_id: string;
      caption_id: string;
      pairing_confirmed: boolean;
    } | null>(null),
    [review, setReview] = useState<{
      submission: Submission;
      kind: string;
    } | null>(null),
    [historical, setHistorical] = useState(true),
    [allowUnverifiedAudio, setAllowUnverifiedAudio] = useState(false),
    [trialNote, setTrialNote] = useState(""),
    [approvalId, setApprovalId] = useState("");
  const [contents, setContents] = useState<
    {
      id: string;
      asset_id: string;
      content_hash: string | null;
      superseded_by: string | null;
      report: { processing_complete: boolean };
    }[]
  >([]);
  const [contentId, setContentId] = useState("");
  const [contentError, setContentError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setContents([]);
    setContentId("");
    setContentError("");
    if (review)
      api<{ items: typeof contents }>(
        `/projects/${data.project.id}/video-contents`,
      )
        .then((result) => {
          if (!cancelled) setContents(result.items);
        })
        .catch((error) => {
          if (!cancelled) setContentError((error as Error).message);
        });
    return () => {
      cancelled = true;
    };
  }, [review, data.project.id]);
  const ruleset = data.rulesets.find(
    (r) => r.id === data.project.current_ruleset_id,
  );
  const newSubmission = (parent?: Submission) =>
    setForm({
      creator: parent?.creator || "",
      parent_id: parent?.id || null,
      script_id: parent?.script_id || "",
      video_id: parent?.video_id || "",
      cover_id: parent?.cover_id || "",
      caption_id: parent?.caption_id || "",
      pairing_confirmed: false,
    });
  const isLatest = (s: Submission) =>
    !data.submissions.some(
      (other) => other.lineage_id === s.lineage_id && other.version > s.version,
    );
  const createReview = (s: Submission, kind: string, independent = false) =>
    act(
      async () => {
        const result = await post<{ run_id: string }>(
          independent
            ? "/human-reviews"
            : kind === "script"
              ? "/script-reviews"
              : "/video-reviews",
          {
            submission_id: s.id,
            ruleset_id: ruleset?.id,
            idempotency_key: jobKey(),
            historical_trial: kind === "video" ? historical : false,
            script_approval_id: approvalId || null,
            content_snapshot_id:
              !independent && kind === "video" ? contentId || null : null,
            allow_unverified_audio:
              !independent &&
              kind === "video" &&
              historical &&
              allowUnverifiedAudio,
            trial_note: trialNote,
          },
        );
        setReview(null);
        onRun(result.run_id);
      },
      independent ? "已建立人工独立审核记录，未执行 AI" : "审核任务已提交",
    );
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">CREATOR SUBMISSIONS</div>
          <h1>达人提交</h1>
          <p>将稿件组成不可覆盖的版本，脚本批准只对明确版本有效。</p>
        </div>
        <button className="primary" onClick={() => newSubmission()}>
          <Plus size={16} />
          新建提交
        </button>
      </div>
      {!ruleset && (
        <div className="notice">
          尚未冻结审核规范。可以整理提交与素材；开始审核前，请先确认“审核规范”。
        </div>
      )}
      <div className="submission-list">
        {data.submissions.length ? (
          data.submissions.map((s) => (
            <article
              className={`panel submission-card ${!isLatest(s) ? "superseded" : ""}`}
              key={s.id}
            >
              <div className="submission-heading">
                <div className="creator-avatar">
                  <UserRound size={20} />
                </div>
                <div>
                  <h3>{s.creator}</h3>
                  <span className="muted small">
                    提交于 {date(s.created_at)} ·{" "}
                    {s.pairing_confirmed
                      ? s.script_id && s.video_id
                        ? "脚本与成片关联已确认"
                        : "当前素材归属已确认"
                      : "素材关联待确认"}
                  </span>
                </div>
                <span className="version-chip">v{s.version}</span>
                {!isLatest(s) && <Badge value="STALE" />}
                <button
                  className="text-button"
                  disabled={busy || !isLatest(s)}
                  onClick={() => newSubmission(s)}
                >
                  <GitBranch size={15} />
                  创建修订版
                </button>
              </div>
              <div className="submission-assets">
                {(["script", "video", "cover", "caption"] as const).map(
                  (kind) => {
                    const id = s[`${kind}_id`];
                    const asset = data.assets.find((a) => a.id === id);
                    return (
                      <button
                        key={kind}
                        className={`submission-asset ${!asset ? "missing" : ""}`}
                        disabled={!asset}
                        onClick={() => asset && onAsset(asset)}
                      >
                        <FileIcon kind={kind} />
                        <span>
                          <small>{label(kind)}</small>
                          <strong>{asset ? asset.filename : "未提供"}</strong>
                        </span>
                        {asset && <ArrowUpRight size={14} />}
                      </button>
                    );
                  },
                )}
              </div>
              <div className="submission-bottom">
                <span className="muted small">
                  {s.parent_id
                    ? "修订会使该提交旧版结论失效。"
                    : "历史附件不会自动与其他脚本或视频配对。"}
                </span>
                <div className="actions">
                  <button
                    disabled={busy || !ruleset || !isLatest(s)}
                    onClick={() =>
                      createReview(s, s.video_id ? "video" : "script", true)
                    }
                  >
                    人工独立审核
                  </button>
                  {s.script_id && (
                    <button
                      disabled={
                        busy || !ruleset || !config.text.ready || !isLatest(s)
                      }
                      onClick={() => createReview(s, "script")}
                    >
                      <Sparkles size={15} />
                      AI 脚本审核
                    </button>
                  )}
                  {s.video_id && (
                    <button
                      className="primary"
                      disabled={
                        busy ||
                        !ruleset ||
                        (!config.video.ready &&
                          !(
                            data.project.mode === "trial" &&
                            ruleset.confirmation_kind === "experimental" &&
                            awaitsAudioVerification(config.video)
                          )) ||
                        !isLatest(s)
                      }
                      onClick={() => {
                        setHistorical(true);
                        setApprovalId("");
                        setAllowUnverifiedAudio(false);
                        setTrialNote("");
                        setReview({ submission: s, kind: "video" });
                      }}
                    >
                      <Sparkles size={15} />
                      AI 成片审核
                    </button>
                  )}
                </div>
              </div>
            </article>
          ))
        ) : (
          <div className="panel">
            <Empty
              title="还没有达人提交"
              description="先上传素材，再把脚本、成片、封面和文案组成一份提交。"
            />
          </div>
        )}
      </div>
      {form && (
        <Modal
          title={form.parent_id ? "创建修订版本" : "新建达人提交"}
          onClose={() => setForm(null)}
        >
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await post(`/projects/${data.project.id}/submissions`, {
                  ...form,
                  script_id: form.script_id || null,
                  video_id: form.video_id || null,
                  cover_id: form.cover_id || null,
                  caption_id: form.caption_id || null,
                });
                setForm(null);
              }, "提交版本已保存");
            }}
          >
            <label>
              达人 / 稿件名称
              <input
                required
                value={form.creator}
                onChange={(e) => setForm({ ...form, creator: e.target.value })}
              />
            </label>
            {(["script", "video", "cover", "caption"] as const).map((kind) => (
              <label key={kind}>
                {label(kind)}
                <select
                  value={form[`${kind}_id`]}
                  onChange={(e) =>
                    setForm({ ...form, [`${kind}_id`]: e.target.value })
                  }
                >
                  <option value="">暂未提供</option>
                  {data.assets
                    .filter((a) => a.kind === kind)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.filename} · {label(a.parse_status)}
                      </option>
                    ))}
                </select>
              </label>
            ))}
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.pairing_confirmed}
                onChange={(e) =>
                  setForm({ ...form, pairing_confirmed: e.target.checked })
                }
              />
              我已确认这些素材属于同一次达人提交
            </label>
            <div className="form-actions">
              <button type="button" onClick={() => setForm(null)}>
                取消
              </button>
              <button className="primary" disabled={busy}>
                保存新版本
              </button>
            </div>
          </form>
        </Modal>
      )}
      {review && (
        <Modal title="提交完整视频审核" onClose={() => setReview(null)}>
          <div className="form-stack">
            <div className="notice compact">
              将使用原始 MP4、独立 ASR、全时间范围连续视频细查和证据复核。AI
              结果仍需人工最终确认。
            </div>
            <label>
              审核方式
              <select
                value={historical ? "historical" : "formal"}
                onChange={(e) => setHistorical(e.target.value === "historical")}
              >
                <option value="historical">
                  历史成片试审（不声明已有脚本批准）
                </option>
                <option value="formal">
                  正式成片审核（需要对应版本脚本批准）
                </option>
              </select>
            </label>
            <label>
              已读取的视频内容版本
              <select
                aria-label="合规审核使用的内容快照"
                value={contentId}
                onChange={(e) => setContentId(e.target.value)}
              >
                <option value="">使用原有连续视频观察流程</option>
                {contents
                  .filter(
                    (item) =>
                      item.asset_id === review.submission.video_id &&
                      item.report.processing_complete &&
                      !item.superseded_by,
                  )
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id.slice(0, 8)} · 候选内容 · SHA{" "}
                      {item.content_hash?.slice(0, 10)}
                    </option>
                  ))}
              </select>
            </label>
            {contentError && (
              <p className="error">内容快照读取失败：{contentError}</p>
            )}
            {contentId && (
              <p className="notice compact">
                将冻结此内容版本的哈希，复用完整音画候选与分歧，再按规范复核证据。此功能仅限技术试审，内容确认不等于品牌批准。
              </p>
            )}
            {!historical && (
              <label>
                批准脚本版本
                <select
                  value={approvalId}
                  onChange={(e) => setApprovalId(e.target.value)}
                >
                  <option value="">选择批准记录</option>
                  {data.script_approvals
                    .filter(
                      (a) =>
                        a.script_id === review.submission.script_id &&
                        a.ruleset_id === ruleset?.id,
                    )
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.id.slice(0, 12)}
                      </option>
                    ))}
                </select>
              </label>
            )}
            {historical &&
              data.project.mode === "trial" &&
              !config.video.ready && (
                <>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={allowUnverifiedAudio}
                      onChange={(e) =>
                        setAllowUnverifiedAudio(e.target.checked)
                      }
                    />
                    音画尚未人工验收，仍运行受限技术试审；保留全部识别分歧，不自动通过。
                  </label>
                  <label>
                    试审限制说明
                    <textarea
                      value={trialNote}
                      onChange={(e) => setTrialNote(e.target.value)}
                      placeholder="记录尚未确认的音画、产品名等限制"
                    />
                  </label>
                </>
              )}
            <div className="form-actions">
              <button onClick={() => setReview(null)}>取消</button>
              <button
                className="primary"
                disabled={
                  busy ||
                  (!historical && (!approvalId || !config.video.ready)) ||
                  (!config.video.ready &&
                    (!allowUnverifiedAudio || trialNote.trim().length < 10))
                }
                onClick={() => createReview(review.submission, "video")}
              >
                开始审核
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
