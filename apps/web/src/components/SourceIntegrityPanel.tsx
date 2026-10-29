import { useCallback, useEffect, useRef, useState } from "react";
import { FileSearch, RefreshCw, ChevronRight } from "lucide-react";
import { api, jobKey, post } from "../api/client";
import type { Asset } from "../api/types";
import { Modal } from "./Common";
import "./source-integrity.css";

type Gap = { code: string; message: string };
type ReadingReport = {
  processing_complete: boolean;
  expected_roots: number;
  processed_roots: number;
  needs_human_units: number;
  semantic_audit: string;
  production_ready: boolean;
  issues?: Gap[];
  scan_progress?: { scanned_pages: number; expected_pages: number } | null;
  units?: number;
  markdown?: { sha256: string; bytes: number; audit: { status: string } } | null;
  validation_rounds?: { id: string; label: string; status: string }[];
};
type ManifestFile = {
  id: string;
  asset_id: string;
  filename: string;
  source_sha256: string;
  status: string;
  report: ReadingReport;
};
type Manifest = {
  id: string;
  name: string;
  status: string;
  report: ReadingReport & {
    file_count: number;
    completed_files: number;
    limits: string[];
  };
  files: ManifestFile[];
};
type SourceUnit = {
  id: string;
  unit_key: string;
  parent_key: string | null;
  kind: string;
  locator: {
    page?: number;
    bbox?: number[];
    part?: string;
    line_start?: number;
  };
  state: string;
  native_text: string;
  ocr_text: string;
  issues: Gap[];
  features: {
    reread_attempts?: { attempt: number; dpi: number; text: string }[];
    blank_candidate?: boolean;
  };
  artifacts: Record<string, string>;
};
type UnitPage = { items: SourceUnit[]; has_more: boolean };
type ManifestList = {
  loaded?: boolean;
  enabled: boolean;
  items: { id: string; name: string; status: string }[];
};
const statusText: Record<string, string> = {
  INGESTED: "已登记",
  READING: "读取中",
  READING_FINISHED: "读取任务结束",
  FAILED: "读取失败",
  BLOCKED: "存在阻塞",
  PENDING: "待处理",
  ALIGNED: "对账一致",
  CANDIDATE: "机器读取候选",
  NEEDS_HUMAN: "须人工核对",
};

export function SourceIntegrityPanel({
  projectId,
  assets,
  onAsset,
}: {
  projectId: string;
  assets: Asset[];
  onAsset: (asset: Asset) => void;
}) {
  const [list, setList] = useState<ManifestList>({ enabled: false, items: [] });
  const [selected, setSelected] = useState("");
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [sources, setSources] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [markdownBusy, setMarkdownBusy] = useState("");
  const [openFile, setOpenFile] = useState<ManifestFile | null>(null);
  const [roots, setRoots] = useState<SourceUnit[]>([]);
  const [rootOffset, setRootOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [pageUnit, setPageUnit] = useState<SourceUnit | null>(null);
  const [regions, setRegions] = useState<SourceUnit[]>([]);
  const [selectedRegion, setSelectedRegion] = useState<SourceUnit | null>(null);
  const [regionsMore, setRegionsMore] = useState(false);
  const [regionOffset, setRegionOffset] = useState(0);
  const [showBoxes, setShowBoxes] = useState(false);
  const [unitLoading, setUnitLoading] = useState(false);
  const scope = useRef(0);
  const sourceRequest = useRef(0);
  const invalidate = useCallback(() => {
    scope.current++;
  }, []);
  const briefs = assets.filter((a) => a.kind === "brief");
  const reload = useCallback(async () => {
    const version = scope.current;
    try {
      const result = await api<ManifestList>(
        `/projects/${projectId}/source-manifests`,
      );
      if (version !== scope.current) return;
      setList({ ...result, loaded: true });
      if (!selected && result.items.length) setSelected(result.items[0].id);
      if (selected) {
        const current = await api<Manifest>(`/source-manifests/${selected}`);
        if (version === scope.current) setManifest(current);
      }
      setError("");
    } catch (e) {
      if (version === scope.current) setError((e as Error).message);
    }
  }, [projectId, selected]);
  useEffect(() => {
    scope.current++;
    sourceRequest.current++;
    setSources([]);
    setList({ enabled: false, items: [] });
    setSelected("");
    setManifest(null);
    setOpenFile(null);
  }, [projectId]);
  useEffect(() => {
    void reload();
    return invalidate;
  }, [reload, invalidate]);
  useEffect(() => {
    if (!manifest || !["INGESTED", "READING"].includes(manifest.status)) return;
    const timer = setInterval(() => void reload(), 2500);
    return () => clearInterval(timer);
  }, [manifest, reload]);
  async function create() {
    const version = scope.current;
    setBusy(true);
    setError("");
    try {
      const result = await post<{ manifest_id: string }>(
        `/projects/${projectId}/source-manifests`,
        {
          name: `Brief 来源读取 · ${sources.length}份`,
          idempotency_key: jobKey(),
          entries: sources.map((id) => ({
            asset_id: id,
            role: "reference",
            platform: "unconfirmed",
          })),
        },
      );
      if (version === scope.current) {
        setSelected(result.manifest_id);
        await reload();
      }
    } catch (e) {
      if (version === scope.current) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function createMarkdown(file: ManifestFile) {
    const version = scope.current;
    setMarkdownBusy(file.id);
    setError("");
    try {
      await post(`/source-manifests/${selected}/files/${file.id}/markdown`);
      if (version === scope.current) await reload();
    } catch (e) {
      if (version === scope.current) setError((e as Error).message);
    } finally {
      setMarkdownBusy("");
    }
  }
  async function loadRoots(file: ManifestFile, offset = 0) {
    const sequence = ++sourceRequest.current;
    setUnitLoading(true);
    setError("");
    try {
      const result = await api<UnitPage>(
        `/source-manifests/${selected}/units?file_id=${file.id}&roots_only=true&offset=${offset}&limit=30`,
      );
      if (sequence !== sourceRequest.current) return;
      setOpenFile(file);
      setRoots(result.items);
      setRootOffset(offset);
      setHasMore(result.has_more);
      setPageUnit(null);
      setRegions([]);
      setSelectedRegion(null);
    } catch (e) {
      if (sequence === sourceRequest.current) setError((e as Error).message);
    } finally {
      if (sequence === sourceRequest.current) setUnitLoading(false);
    }
  }
  async function loadRegions(root: SourceUnit, offset = 0) {
    if (!openFile) return;
    const sequence = ++sourceRequest.current;
    setUnitLoading(true);
    setError("");
    try {
      const result = await api<UnitPage>(
        `/source-manifests/${selected}/units?file_id=${openFile.id}&parent_key=${encodeURIComponent(root.unit_key)}&offset=${offset}&limit=50`,
      );
      if (sequence !== sourceRequest.current) return;
      setPageUnit(root);
      setRegions(result.items);
      setSelectedRegion(null);
      setRegionOffset(offset);
      setRegionsMore(result.has_more);
    } catch (e) {
      if (sequence === sourceRequest.current) setError((e as Error).message);
    } finally {
      if (sequence === sourceRequest.current) setUnitLoading(false);
    }
  }
  const displayed = selectedRegion || pageUnit;
  const imageUnit = pageUnit?.artifacts.image ? pageUnit : null;
  return (
    <details className="panel integrity-panel" open>
      <summary className="panel-heading">
        <h2>
          <FileSearch size={18} />
          来源完整性检查
        </h2>
        <span className="version-chip">G0 / G1 预览</span>
      </summary>
      <div className="integrity-body">
        <p className="integrity-boundary">
          分别核对文件、页与区域。读取任务结束不代表要求已提全；本模块不会修改已有规范或生成生产批准。
        </p>
        {error && (
          <div className="notice danger" role="alert">
            {error}
          </div>
        )}
        <div className="integrity-source-select">
          <div>
            {briefs.map((asset) => (
              <label className="checkbox" key={asset.id}>
                <input
                  type="checkbox"
                  checked={sources.includes(asset.id)}
                  disabled={busy}
                  onChange={(e) =>
                    setSources(
                      e.target.checked
                        ? [...sources, asset.id]
                        : sources.filter((id) => id !== asset.id),
                    )
                  }
                />
                <span>{asset.filename}</span>
              </label>
            ))}
          </div>
          <button
            className="quiet-primary"
            disabled={!list.enabled || busy || !sources.length}
            onClick={() => void create()}
          >
            {busy ? "正在登记…" : "检查所选 Brief"}
          </button>
        </div>
        <p className="small muted">
          仅登记为参考来源、平台未确认；不会据此声明业务材料集合完整。
          {list.loaded && !list.enabled && "来源读取预览开关未启用。"}
        </p>
        <div className="integrity-controls">
          <label>
            读取记录
            <select
              aria-label="选择来源读取记录"
              value={selected}
              onChange={(e) => {
                setManifest(null);
                setSelected(e.target.value);
                setOpenFile(null);
                sourceRequest.current++;
              }}
            >
              <option value="">
                {error ? "读取失败，请刷新重试" : !list.loaded ? "正在读取来源记录…" : list.items.length ? "请选择读取记录" : "暂无读取记录"}
              </option>
              {list.items.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · {statusText[m.status] || m.status} ·{" "}
                  {m.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => void reload()} disabled={busy}>
            <RefreshCw size={14} />
            刷新读取状态
          </button>
        </div>
        {manifest && (
          <>
            <div className="integrity-stats">
              <div>
                <strong>
                  {manifest.report.completed_files} /{" "}
                  {manifest.report.file_count}
                </strong>
                <span>文件处理对账</span>
              </div>
              <div>
                <strong>
                  {manifest.report.processed_roots} /{" "}
                  {manifest.report.expected_roots}
                </strong>
                <span>已入库页 / 主单元</span>
              </div>
              <div>
                <strong>{manifest.report.needs_human_units}</strong>
                <span>机器发现待核对单元</span>
              </div>
              <div>
                <strong>未开始</strong>
                <span>条款提取 / 语义审计</span>
              </div>
            </div>
            <p className="integrity-not-ready">
              {statusText[manifest.status] || manifest.status} ·{" "}
              {manifest.report.processing_complete
                ? "已登记处理集合对齐"
                : "处理集合尚未对齐"}{" "}
              · 尚无人工来源签认 · 未获生产资格
            </p>
            {manifest.report.issues?.map((gap) => (
              <p className="integrity-gap" key={gap.code}>
                {gap.message}（{gap.code}）
              </p>
            ))}
            <div className="integrity-files">
              {manifest.files.map((file) => (
                <article key={file.id}>
                  <div>
                    <strong>{file.filename}</strong>
                    <span className="small muted">
                      SHA256 {file.source_sha256.slice(0, 16)}… ·{" "}
                      {statusText[file.status] || file.status}
                    </span>
                  </div>
                  <p>
                    {file.report.processed_roots} / {file.report.expected_roots}{" "}
                    主单元 · {file.report.units || 0} 个检测单元 ·{" "}
                    {file.report.needs_human_units} 个须核对
                  </p>
                  <p className="integrity-rounds">
                    {file.report.validation_rounds?.map((round, index) => (
                      <span key={round.id}>{index + 1}. {round.label}：{statusText[round.status] || round.status}</span>
                    ))}
                  </p>
                  {file.report.scan_progress && (
                    <p className="small">
                      正在扫描 {file.report.scan_progress.scanned_pages} /{" "}
                      {file.report.scan_progress.expected_pages}{" "}
                      页，当前文件产物尚未入库
                    </p>
                  )}
                  {file.report.issues?.map((gap) => (
                    <p className="integrity-gap" key={gap.code}>
                      {gap.message}（{gap.code}）
                    </p>
                  ))}
                  <div className="actions">
                    <button
                      disabled={!file.report.processed_roots || unitLoading}
                      onClick={() => void loadRoots(file)}
                    >
                      查看页与区域 <ChevronRight size={14} />
                    </button>
                    {file.report.markdown ? (
                      <a className="button small" href={`/api/v1/source-manifests/${selected}/files/${file.id}/markdown`} download>
                        导出读取 MD
                      </a>
                    ) : (
                      <button disabled={!file.report.processing_complete || !!markdownBusy} onClick={() => void createMarkdown(file)}>
                        {markdownBusy === file.id ? "正在回查 MD…" : "生成读取 MD"}
                      </button>
                    )}
                    <button
                      className="text-button small"
                      onClick={() => {
                        const asset = assets.find(
                          (a) => a.id === file.asset_id,
                        );
                        if (asset) onAsset(asset);
                      }}
                    >
                      查看原件
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </div>
      {openFile && (
        <Modal
          wide
          title={`来源读取 · ${openFile.filename}`}
          onClose={() => {
            sourceRequest.current++;
            setOpenFile(null);
          }}
        >
          <p className="small muted">
            原文、OCR和区域框供人工比对；高OCR置信或两路一致不等于语义完整。
          </p>
          <div className="integrity-root-picker">
            <label>
              选择页 / 主单元
              <select
                aria-label="选择读取页或主单元"
                value={pageUnit?.id || ""}
                disabled={unitLoading}
                onChange={(e) => {
                  const root = roots.find((u) => u.id === e.target.value);
                  if (root) void loadRegions(root);
                }}
              >
                <option value="">请选择</option>
                {roots.map((root) => (
                  <option key={root.id} value={root.id}>
                    {root.locator.page
                      ? `第 ${root.locator.page} 页`
                      : root.unit_key}{" "}
                    · {statusText[root.state] || root.state}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={!rootOffset || unitLoading}
              onClick={() => void loadRoots(openFile, rootOffset - 30)}
            >
              上一组主单元
            </button>
            <button
              disabled={!hasMore || unitLoading}
              onClick={() => void loadRoots(openFile, rootOffset + 30)}
            >
              下一组主单元
            </button>
          </div>
          {unitLoading && <p role="status">正在读取单元…</p>}
          {pageUnit && (
            <>
              <div className="integrity-reading-grid">
                <div>
                  {imageUnit ? (
                    <>
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={showBoxes}
                          onChange={(e) => setShowBoxes(e.target.checked)}
                        />
                        显示当前组检测区域框
                      </label>
                      <div className="integrity-canvas">
                        <img
                          alt={`独立读取原页${pageUnit.locator.page || ""}`}
                          src={`/api/v1/source-manifests/${selected}/units/${imageUnit.id}/artifacts/image`}
                        />
                        {(showBoxes
                          ? regions
                          : selectedRegion
                            ? [selectedRegion]
                            : []
                        )
                          .filter((u) => u.locator.bbox)
                          .map((u) => {
                            const box = u.locator.bbox!;
                            return (
                              <button
                                key={u.id}
                                aria-label={`定位区域 ${u.unit_key}`}
                                title={u.unit_key}
                                className={`integrity-region ${selectedRegion?.id === u.id ? "selected" : ""}`}
                                style={{
                                  left: `${box[0] * 100}%`,
                                  top: `${box[1] * 100}%`,
                                  width: `${(box[2] - box[0]) * 100}%`,
                                  height: `${(box[3] - box[1]) * 100}%`,
                                }}
                                onClick={() => setSelectedRegion(u)}
                              />
                            );
                          })}
                      </div>
                    </>
                  ) : (
                    <p className="notice compact">
                      此格式按结构定位，未伪造物理页或渲染图。
                    </p>
                  )}
                </div>
                <div className="integrity-texts">
                  <div className="section-line">
                    <strong>
                      {selectedRegion ? "区域内容" : "整页 / 主单元内容"}
                    </strong>
                    {selectedRegion && (
                      <button
                        className="text-button small"
                        onClick={() => setSelectedRegion(null)}
                      >
                        返回主单元
                      </button>
                    )}
                  </div>
                  <p className="small muted">
                    {displayed?.unit_key} · {statusText[displayed?.state || ""]}
                  </p>
                  <h3>原生 / 结构文字</h3>
                  <pre>
                    {displayed?.native_text ||
                      "此单元未读取到原生文字，不能据此判断无要求。"}
                  </pre>
                  <h3>独立视觉 OCR</h3>
                  <pre>
                    {displayed?.ocr_text ||
                      "此单元无视觉OCR正文；图像、版面或空白候选仍需核对。"}
                  </pre>
                  {displayed?.issues.map((gap) => (
                    <p className="integrity-gap" key={gap.code}>
                      {gap.message}（{gap.code}）
                    </p>
                  ))}
                  {displayed?.features.reread_attempts?.map((attempt) => (
                    <details key={attempt.attempt}>
                      <summary>
                        区域重读 {attempt.attempt} · {attempt.dpi} DPI
                      </summary>
                      <pre>{attempt.text || "仍未读清"}</pre>
                    </details>
                  ))}
                </div>
              </div>
              <div className="integrity-regions-list">
                <h3>
                  当前页检测区域（第 {Math.floor(regionOffset / 50) + 1} 组）
                </h3>
                {regions.map((u) => (
                  <button
                    key={u.id}
                    className={selectedRegion?.id === u.id ? "selected" : ""}
                    onClick={() => setSelectedRegion(u)}
                  >
                    <span>
                      {u.kind} · {statusText[u.state] || u.state}
                    </span>
                    <p>
                      {(
                        u.native_text ||
                        u.ocr_text ||
                        u.issues[0]?.message ||
                        u.unit_key
                      ).slice(0, 140)}
                    </p>
                  </button>
                ))}
              </div>
              <div className="actions">
                <button
                  disabled={!regionOffset || unitLoading}
                  onClick={() => void loadRegions(pageUnit, regionOffset - 50)}
                >
                  上一组区域
                </button>
                <button
                  disabled={!regionsMore || unitLoading}
                  onClick={() => void loadRegions(pageUnit, regionOffset + 50)}
                >
                  下一组区域
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </details>
  );
}
