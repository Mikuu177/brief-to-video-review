import { useEffect, useState } from "react";
import {
  Plus,
  Sparkles,
  LockKeyhole,
  ExternalLink,
  Pencil,
  Check,
  Braces,
} from "lucide-react";
import { api, jobKey, post } from "../api/client";
import type {
  Asset,
  Block,
  Conflict,
  Rule,
  RuleSet,
  WorkspaceProps,
} from "../api/types";
import { Badge, Empty, Modal, label } from "../components/Common";
import { locatorText } from "../components/SourceViewer";
import { SourceIntegrityPanel } from "../components/SourceIntegrityPanel";

export default function RulesPage({
  data,
  config,
  busy,
  act,
  onAsset,
  onRun,
}: WorkspaceProps) {
  const [selectedId, setSelectedId] = useState(""),
    [sources, setSources] = useState<string[]>([]),
    [blocks, setBlocks] = useState<(Block & { asset: Asset })[]>([]),
    [editing, setEditing] = useState<Rule | null>(null),
    [confirm, setConfirm] = useState(false),
    [note, setNote] = useState(""),
    [confirmationKind, setConfirmationKind] = useState("experimental"),
    [acceptPartialSources, setAcceptPartialSources] = useState(false),
    [advanced, setAdvanced] = useState(false),
    [json, setJson] = useState(""),
    [filter, setFilter] = useState("all");
  const selected =
    data.rulesets.find((r) => r.id === selectedId) || data.rulesets[0];
  const briefs = data.assets.filter((a) => a.kind === "brief");
  const sourceIds = selected?.source_ids || sources;
  const sourceKey = sourceIds.join(",");
  useEffect(() => {
    let active = true;
    Promise.all(
      sourceKey
        .split(",")
        .filter(Boolean)
        .map(async (id) => {
          const asset = data.assets.find((a) => a.id === id)!;
          const response = await api<{ blocks: Block[] }>(
            `/assets/${id}/blocks`,
          );
          return response.blocks.map((b) => ({ ...b, asset }));
        }),
    )
      .then((rows) => {
        if (active) setBlocks(rows.flat());
      })
      .catch(() => {
        if (active) setBlocks([]);
      });
    return () => {
      active = false;
    };
  }, [sourceKey, data.assets]);
  async function saveRules(
    rules: Rule[],
    conflicts: Conflict[] = selected.conflicts,
  ) {
    const updated = await api<RuleSet>(`/rulesets/${selected.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: selected.revision,
        rules,
        conflicts,
      }),
    });
    setSelectedId(updated.id);
    return updated;
  }
  function createRule() {
    setEditing({
      id: `R-${crypto.randomUUID().slice(0, 8)}`,
      title: "",
      type: "must",
      severity: "major",
      scope: {
        platforms: [data.project.platform],
        phases: [data.project.phase],
        asset_kinds: ["video"],
      },
      predicate: {
        kind: "text_semantic",
        instruction: "",
        terms: [],
        window_ms: null,
        manual_required: false,
      },
      source_refs: [{ block_id: blocks[0]?.id || "", quote: "" }],
      exceptions: [],
      relations: [],
      confirmation_status: "pending",
      confirmation_note: "",
    });
  }
  const shown =
    selected?.rules.filter(
      (r) => filter === "all" || r.confirmation_status === filter,
    ) || [];
  const incompleteCoverage =
    selected?.extraction_coverage.filter(
      (entry) => !["success", "blank"].includes(entry.status),
    ) || [];
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">BRIEF → REVIEW STANDARD</div>
          <h1>审核规范</h1>
          <p>先确认要求、适用范围和冲突，再交给 AI 审核。</p>
        </div>
        <div className="actions">
          <button
            disabled={busy || !selected}
            onClick={() => {
              setJson(
                JSON.stringify(
                  { rules: selected.rules, conflicts: selected.conflicts },
                  null,
                  2,
                ),
              );
              setAdvanced(true);
            }}
          >
            <Braces size={16} />
            完整结构
          </button>
          <button
            className="primary"
            disabled={busy || !selected}
            onClick={() => setConfirm(true)}
          >
            <LockKeyhole size={16} />
            确认并冻结
          </button>
        </div>
      </div>
      <SourceIntegrityPanel
        projectId={data.project.id}
        assets={data.assets}
        onAsset={onAsset}
      />
      <div className="panel rules-start">
        <div>
          <strong>来源 Brief</strong>
          <div className="brief-options">
            {briefs.map((a) => (
              <label className="checkbox" key={a.id}>
                <input
                  type="checkbox"
                  checked={sources.includes(a.id)}
                  disabled={busy}
                  onChange={(e) =>
                    setSources(
                      e.target.checked
                        ? [...sources, a.id]
                        : sources.filter((id) => id !== a.id),
                    )
                  }
                />
                <span>{a.filename}</span>
                <Badge value={a.parse_status} />
              </label>
            ))}
          </div>
        </div>
        <div className="actions">
          <button
            disabled={busy || !sources.length}
            onClick={() =>
              act(async () => {
                const draft = await post<RuleSet>(
                  `/projects/${data.project.id}/rulesets/manual`,
                  { source_ids: sources, idempotency_key: jobKey() },
                );
                setSelectedId(draft.id);
              }, "人工草案已创建")
            }
          >
            创建人工草案
          </button>
          <button
            className="primary"
            disabled={busy || !sources.length || !config.text.ready}
            title={!config.text.ready ? "等待模型配置" : ""}
            onClick={() =>
              act(async () => {
                const result = await post<{
                  run_id: string;
                  ruleset_id: string;
                }>(`/projects/${data.project.id}/rulesets/extract`, {
                  source_ids: sources,
                  idempotency_key: jobKey(),
                });
                setSelectedId(result.ruleset_id);
                onRun(result.run_id);
              }, "AI 规范提取任务已提交")
            }
          >
            <Sparkles size={16} />
            {config.text.ready ? "AI 提取规范" : "AI 提取 · 待配置"}
          </button>
        </div>
      </div>
      {!config.text.ready && (
        <div className="notice">
          AI
          尚未配置。可以查看原件、整理人工草案；所有草案都需要逐页核对与确认。

        </div>
      )}
      {selected ? (
        <>
          <div className="section-line">
            <div className="actions">
              <h2>规则工作台</h2>
              <select
                aria-label="选择规范版本"
                className="compact-select"
                value={selected.id}
                onChange={(e) => setSelectedId(e.target.value)}
              >
                {data.rulesets.map((r, i) => (
                  <option value={r.id} key={r.id}>
                    版本 {data.rulesets.length - i} · {label(r.status)} ·{" "}
                    {r.rules.length} 条
                  </option>
                ))}
              </select>
              <Badge value={selected.status} />
              {selected.status === "FROZEN" && (
                <Badge value="confirmed">
                  {selected.confirmation_kind === "brand_confirmed"
                    ? "品牌确认规范"
                    : "技术试验规范"}
                </Badge>
              )}
            </div>
            <button disabled={busy} onClick={createRule}>
              <Plus size={15} />
              添加规则
            </button>
          </div>
          <div className="rule-stats">
            <div>
              <strong>{selected.rules.length}</strong>
              <span>规则总数</span>
            </div>
            <div>
              <strong>
                {
                  selected.rules.filter(
                    (r) => r.confirmation_status === "pending",
                  ).length
                }
              </strong>
              <span>待人工确认</span>
            </div>
            <div>
              <strong>
                {
                  selected.conflicts.filter(
                    (c) => c.status === "pending" && c.blocking,
                  ).length
                }
              </strong>
              <span>关键冲突</span>
            </div>
            <div>
              <strong>
                {selected.extraction_coverage.length}{" "}
                <small>
                  /{" "}
                  {selected.source_ids.reduce(
                    (n, id) =>
                      n +
                      (data.assets.find((a) => a.id === id)?.coverage.length ||
                        0),
                    0,
                  )}
                </small>
              </strong>
              <span>已登记处理页 / 表</span>
            </div>
          </div>
          {incompleteCoverage.length > 0 && (
            <div className="panel">
              <div className="notice">
                仍有 {incompleteCoverage.length} 页 / 表存在读取限制，
                已处理不代表完整可读。请核对原页并保留限制说明。
              </div>
              {incompleteCoverage.map((entry) => {
                const source = blocks.find(
                  (block) =>
                    block.asset.id === entry.asset_id &&
                    String(block.locator.page ?? block.locator.sheet) ===
                      String(entry.unit),
                );
                const asset = data.assets.find(
                  (item) => item.id === entry.asset_id,
                );
                return (
                  <div
                    className="section-line"
                    key={`${entry.asset_id}:${entry.unit}`}
                  >
                    <div>
                      <strong>
                        {asset?.filename} · 页 / 表 {entry.unit}
                      </strong>
                      <p>
                        {entry.limitations?.length
                          ? entry.limitations.join("；")
                          : "读取未完整，请查看原件。"}
                      </p>
                    </div>
                    <button
                      disabled={!source}
                      onClick={() => source && onAsset(source.asset, source.id)}
                    >
                      查看原页
                    </button>
                  </div>
                );
              })}
            </div>
          )}
          {selected.conflicts.map((conflict) => (
            <div
              className={`conflict-card ${conflict.status === "resolved" ? "resolved" : ""}`}
              key={conflict.id}
            >
              <div>
                <strong>
                  {conflict.status === "resolved" ? "已裁定" : "待裁定"} ·
                  口径与范围
                </strong>
                <p>{conflict.description}</p>
                {conflict.resolution && (
                  <p className="muted">裁定：{conflict.resolution}</p>
                )}
              </div>
              <button
                disabled={busy || selected.status === "FROZEN"}
                onClick={() => {
                  setNote(conflict.resolution);
                  setJson(conflict.id);
                  setConfirmationKind("conflict");
                  setConfirm(true);
                }}
              >
                记录裁定
              </button>
            </div>
          ))}
          <div className="panel">
            <div className="panel-toolbar">
              <div className="tabs">
                {["all", "pending", "confirmed", "excluded"].map((k) => (
                  <button
                    className={filter === k ? "active" : ""}
                    onClick={() => setFilter(k)}
                    key={k}
                  >
                    {k === "all" ? "全部规则" : label(k)}
                  </button>
                ))}
              </div>
              <span className="muted small">点击出处查看原页</span>
            </div>
            {shown.length ? (
              shown.map((rule) => (
                <article className="rule-card" key={rule.id}>
                  <div className="rule-main">
                    <div className="rule-card-title">
                      <span className="rule-code">{rule.id}</span>
                      <Badge value={rule.severity} />
                      <Badge value={rule.confirmation_status} />
                    </div>
                    <h3>{rule.title}</h3>
                    <p>{rule.predicate.instruction}</p>
                    <div className="scope-tags">
                      <span>{label(rule.type)}</span>
                      {rule.scope.asset_kinds.map((k) => (
                        <span key={k}>{label(k)}</span>
                      ))}
                      {rule.scope.platforms.map((k) => (
                        <span key={k}>{label(k)}</span>
                      ))}
                      {rule.scope.phases.map((k) => (
                        <span key={k}>{label(k)}</span>
                      ))}
                    </div>
                    {rule.source_refs.map((ref, i) => {
                      const block = blocks.find((b) => b.id === ref.block_id);
                      return (
                        <button
                          key={i}
                          className="citation"
                          disabled={!block}
                          onClick={() =>
                            block && onAsset(block.asset, block.id)
                          }
                        >
                          <span>
                            Brief 出处 {block ? locatorText(block) : "来源块"}
                          </span>
                          <q>{ref.quote}</q>
                          <ExternalLink size={14} />
                        </button>
                      );
                    })}
                  </div>
                  <div className="rule-buttons">
                    <button
                      aria-label={`编辑 ${rule.title}`}
                      disabled={busy}
                      onClick={() => setEditing(structuredClone(rule))}
                    >
                      <Pencil size={16} />
                    </button>
                    {rule.confirmation_status === "pending" &&
                      selected.status !== "FROZEN" && (
                        <button
                          className="quiet-primary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              () =>
                                saveRules(
                                  selected.rules.map((r) =>
                                    r.id === rule.id
                                      ? {
                                          ...r,
                                          confirmation_status: "confirmed",
                                        }
                                      : r,
                                  ),
                                ),
                              "规则已确认",
                            )
                          }
                        >
                          <Check size={15} />
                          确认
                        </button>
                      )}
                  </div>
                </article>
              ))
            ) : (
              <Empty
                title="草案尚无规则"
                description="接入模型后提取，或从原文添加有引用依据的人工规则。"
              />
            )}
          </div>
          <div className="section-line">
            <span className="muted small">
              冻结后修改将生成新版本，已有审核记录会按依赖标记过期。
            </span>
            <button
              disabled={busy || selected.status !== "DRAFT"}
              onClick={() => {
                setNote("");
                setConfirmationKind("coverage");
                setConfirm(true);
              }}
            >
              登记人工逐页检查
            </button>
          </div>
        </>
      ) : (
        <Empty
          title="尚未创建审核规范"
          description="选择主 Brief 后创建草案。不同平台的要求应明确区分。"
        />
      )}
      {editing && (
        <Modal title="编辑审核规则" wide onClose={() => setEditing(null)}>
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await saveRules(
                  selected.rules.some((r) => r.id === editing.id)
                    ? selected.rules.map((r) =>
                        r.id === editing.id ? editing : r,
                      )
                    : [...selected.rules, editing],
                );
                setEditing(null);
              }, "规则已保存");
            }}
          >
            <label>
              规则标题
              <input
                required
                value={editing.title}
                onChange={(e) =>
                  setEditing({ ...editing, title: e.target.value })
                }
              />
            </label>
            <label>
              审核要求
              <textarea
                required
                value={editing.predicate.instruction}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    predicate: {
                      ...editing.predicate,
                      instruction: e.target.value,
                    },
                  })
                }
              />
            </label>
            <div className="form-grid">
              <label>
                类型
                <select
                  value={editing.type}
                  onChange={(e) =>
                    setEditing({ ...editing, type: e.target.value })
                  }
                >
                  {[
                    "must",
                    "should",
                    "fact",
                    "example",
                    "exception",
                    "out_of_scope",
                  ].map((v) => (
                    <option key={v} value={v}>
                      {label(v)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                严重程度
                <select
                  value={editing.severity}
                  onChange={(e) =>
                    setEditing({ ...editing, severity: e.target.value })
                  }
                >
                  {["blocker", "major", "minor", "advisory"].map((v) => (
                    <option key={v} value={v}>
                      {label(v)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <fieldset className="scope-fieldset">
              <legend>适用素材</legend>
              <div className="scope-choice">
                {["script", "video", "cover", "caption", "image_post"].map(
                  (k) => (
                    <label className="checkbox" key={k}>
                      <input
                        type="checkbox"
                        checked={editing.scope.asset_kinds.includes(k)}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            scope: {
                              ...editing.scope,
                              asset_kinds: e.target.checked
                                ? [...editing.scope.asset_kinds, k]
                                : editing.scope.asset_kinds.filter(
                                    (v) => v !== k,
                                  ),
                            },
                          })
                        }
                      />
                      {label(k)}
                    </label>
                  ),
                )}
              </div>
            </fieldset>
            <div className="form-grid">
              <label>
                平台（逗号分隔）
                <input
                  value={editing.scope.platforms.join(",")}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      scope: {
                        ...editing.scope,
                        platforms: e.target.value.split(","),
                      },
                    })
                  }
                />
              </label>
              <label>
                阶段（逗号分隔）
                <input
                  value={editing.scope.phases.join(",")}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      scope: {
                        ...editing.scope,
                        phases: e.target.value.split(","),
                      },
                    })
                  }
                />
              </label>
            </div>
            <label>
              来源位置
              <select
                value={editing.source_refs[0]?.block_id || ""}
                onChange={(e) => {
                  const b = blocks.find((b) => b.id === e.target.value);
                  setEditing({
                    ...editing,
                    source_refs: [
                      {
                        block_id: e.target.value,
                        quote: b?.text.slice(0, 2000) || "",
                      },
                    ],
                  });
                }}
              >
                <option value="">请选择来源块</option>
                {blocks.map((b) => (
                  <option key={b.id} value={b.id}>
                    {locatorText(b)} · {b.text.slice(0, 65)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              原文引文
              <textarea
                required
                value={editing.source_refs[0]?.quote || ""}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    source_refs: [
                      {
                        block_id: editing.source_refs[0]?.block_id || "",
                        quote: e.target.value,
                      },
                    ],
                  })
                }
              />
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={editing.predicate.manual_required}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    predicate: {
                      ...editing.predicate,
                      manual_required: e.target.checked,
                    },
                  })
                }
              />
              此项始终需要人工判断
            </label>
            <div className="form-actions">
              <button type="button" onClick={() => setEditing(null)}>
                取消
              </button>
              <button className="primary" disabled={busy} type="submit">
                保存规则
              </button>
            </div>
          </form>
        </Modal>
      )}
      {advanced && (
        <Modal title="完整规则结构" wide onClose={() => setAdvanced(false)}>
          <p className="muted">
            可编辑 predicate、例外、关系与全部来源引用。保存时执行结构校验。
          </p>
          <textarea
            className="json-editor"
            value={json}
            onChange={(e) => setJson(e.target.value)}
          />
          <div className="form-actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const value = JSON.parse(json) as {
                    rules: Rule[];
                    conflicts: Conflict[];
                  };
                  await saveRules(value.rules, value.conflicts);
                  setAdvanced(false);
                }, "规范结构已保存")
              }
            >
              校验并保存
            </button>
          </div>
        </Modal>
      )}
      {confirm && (
        <Modal
          title={
            confirmationKind === "conflict"
              ? "记录口径裁定"
              : confirmationKind === "coverage"
                ? "登记人工逐页核对"
                : "确认并冻结规范"
          }
          onClose={() => {
            setConfirm(false);
            setConfirmationKind("experimental");
          }}
        >
          <div className="form-stack">
            <div className="notice compact">
              {confirmationKind === "coverage"
                ? "此操作声明你已逐页检查全部来源，包含图片、扫描页和表格，并完整整理要求。系统会保存声明人、理由和原件哈希；解析限制仍保留。只看过局部参考时，请继续检查剩余页面。"
                : confirmationKind === "conflict"
                  ? "保留裁定理由，明确适用口径；系统不会替品牌默默放宽限制。"
                  : "冻结前检查全部规则、来源覆盖和关键冲突。局部参考草案不能直接当成完整规范。"}
            </div>
            {!["conflict", "coverage"].includes(confirmationKind) && (
              <label>
                确认身份
                <select
                  value={confirmationKind}
                  onChange={(e) => setConfirmationKind(e.target.value)}
                >
                  <option value="experimental">技术试验规范</option>
                  <option value="brand_confirmed">品牌确认规范</option>
                </select>
              </label>
            )}
            <label>
              说明与依据
              <textarea
                required
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="写明核对范围、裁定理由或确认依据"
              />
            </label>
            {confirmationKind === "experimental" &&
              data.project.mode === "trial" && (
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={acceptPartialSources}
                    onChange={(e) => setAcceptPartialSources(e.target.checked)}
                  />
                  接受已处理页面的局部读取限制，仅用于受限技术试审。保留未读清记录，不声明人工已逐页核对，结果不得自动通过。
                </label>
              )}
            <div className="form-actions">
              <button onClick={() => setConfirm(false)}>取消</button>
              <button
                className="primary"
                disabled={busy || note.length < 10}
                onClick={() =>
                  act(async () => {
                    if (confirmationKind === "conflict")
                      await saveRules(
                        selected.rules,
                        selected.conflicts.map((c) =>
                          c.id === json
                            ? { ...c, status: "resolved", resolution: note }
                            : c,
                        ),
                      );
                    else if (confirmationKind === "coverage")
                      await post(`/rulesets/${selected.id}/manual-coverage`, {
                        expected_revision: selected.revision,
                        note,
                      });
                    else
                      await post(`/rulesets/${selected.id}/confirm`, {
                        expected_revision: selected.revision,
                        note,
                        kind: confirmationKind,
                        accept_partial_sources:
                          confirmationKind === "experimental" &&
                          acceptPartialSources,
                      });
                    setConfirm(false);
                    setConfirmationKind("experimental");
                    setNote("");
                    setAcceptPartialSources(false);
                  }, "确认记录已保存")
                }
              >
                确认记录
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
