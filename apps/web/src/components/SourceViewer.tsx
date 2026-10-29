import { useEffect, useState } from "react";
import { ExternalLink, ChevronLeft, ChevronRight } from "lucide-react";
import { api, mediaUrl } from "../api/client";
import type { Asset, Block } from "../api/types";
import { Badge, Empty, Loading } from "./Common";
import { ScriptViewer, type ScriptStructure } from "./ScriptViewer";

export function locatorText(block: Block) {
  const l = block.locator;
  if (l.kind === "pdf") return `第 ${l.page} 页`;
  if (l.kind === "xlsx")
    return `${l.sheet} · ${l.cell}${l.merged_range ? ` (${l.merged_range})` : ""}`;
  if (l.kind === "txt") return `第 ${l.line_start} 行`;
  return l.table_index !== undefined
    ? `表 ${l.table_index + 1} / 行 ${(l.row ?? 0) + 1} / 列 ${(l.column ?? 0) + 1}`
    : `段落 ${(l.paragraph_index ?? 0) + 1}`;
}
export function SourceViewer({
  asset,
  focusId,
  onSelect,
}: {
  asset: Asset;
  focusId?: string;
  onSelect?: (block: Block) => void;
}) {
  const [blocks, setBlocks] = useState<Block[]>([]),
    [structure, setStructure] = useState<ScriptStructure | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [page, setPage] = useState(1),
    [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<{ blocks: Block[]; script_structure: ScriptStructure | null }>(
      `/assets/${asset.id}/blocks`,
    )
      .then((d) => {
        if (active) {
          setBlocks(d.blocks);
          setStructure(d.script_structure);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [asset.id, asset.parse_status]);
  useEffect(() => {
    if (focusId) {
      const block = blocks.find((b) => b.id === focusId);
      if (block) {
        setSelected(block.id);
        if (block.locator.page) setPage(block.locator.page);
      }
    }
  }, [focusId, blocks]);
  useEffect(() => {
    if (selected)
      document
        .getElementById(`source-${selected}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [selected, page, blocks]);
  const current = blocks.find((b) => b.id === selected);
  if (asset.kind === "video")
    return (
      <div className="source-media">
        <video controls preload="metadata" src={mediaUrl(asset.id)} />
        <p>
          完整原始 MP4 ·{" "}
          {asset.metadata.has_audio ? "含音轨" : "音轨状态见媒体信息"} ·
          尚未据此产生 AI 审核结论
        </p>
      </div>
    );
  if (asset.kind === "cover")
    return (
      <div className="source-media">
        <img src={mediaUrl(asset.id)} alt="上传的独立封面" />
      </div>
    );
  if (loading) return <Loading />;
  if (error) return <div className="notice danger">{error}</div>;
  const filtered =
    asset.mime === "application/pdf"
      ? blocks.filter((b) => b.locator.page === page)
      : blocks;
  return (
    <div
      className={`source-viewer ${asset.mime === "application/pdf" ? "with-page" : ""}`}
    >
      <div className="source-text">
        <div className="source-bar">
          <span>{blocks.length} 个来源块</span>
          <a href={mediaUrl(asset.id)} target="_blank" rel="noreferrer">
            打开原件 <ExternalLink size={13} />
          </a>
        </div>
        {asset.metadata.limits?.map((limit, i) => (
          <div className="notice compact" key={i}>
            {limit}
          </div>
        ))}
        {structure && (
          <ScriptViewer
            structure={structure}
            onSource={(id) => {
              setSelected(id);
              document
                .getElementById(`source-${id}`)
                ?.scrollIntoView({ block: "nearest" });
            }}
          />
        )}
        {filtered.length ? (
          filtered.map((b) => (
            <button
              type="button"
              id={`source-${b.id}`}
              key={b.id}
              className={`source-block ${selected === b.id ? "selected" : ""}`}
              onClick={() => {
                setSelected(b.id);
                onSelect?.(b);
              }}
            >
              <span className="source-locator">
                {locatorText(b)} <Badge value={b.coverage_status} />
              </span>
              <span className="source-content">{b.text}</span>
              {b.locator.image_key && (
                <img
                  className="embedded-image"
                  src={`/api/v1/assets/${asset.id}/embedded/${b.id}`}
                  alt={`${b.locator.cell} 内嵌图片`}
                />
              )}
            </button>
          ))
        ) : (
          <Empty
            title="本页暂无可读来源块"
            description="请查看原页，并核对解析覆盖状态。"
          />
        )}
      </div>
      {asset.mime === "application/pdf" && (
        <div className="pdf-pane">
          <div className="page-control">
            <button
              aria-label="上一页"
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
            >
              <ChevronLeft size={18} />
            </button>
            <span>
              原件第{" "}
              <input
                aria-label="PDF页码"
                type="number"
                min="1"
                max={asset.metadata.page_count || 1}
                value={page}
                onChange={(e) =>
                  setPage(
                    Math.max(
                      1,
                      Math.min(
                        asset.metadata.page_count || 1,
                        Number(e.target.value) || 1,
                      ),
                    ),
                  )
                }
              />{" "}
              / {asset.metadata.page_count} 页
            </span>
            <button
              aria-label="下一页"
              disabled={page === (asset.metadata.page_count || 1)}
              onClick={() => setPage(page + 1)}
            >
              <ChevronRight size={18} />
            </button>
          </div>
          <div className="pdf-image">
            <img
              src={`/api/v1/assets/${asset.id}/preview/${page}`}
              alt={`Brief 原件第${page}页`}
            />
            {current?.locator.page === page && current.locator.bbox && (
              <span
                className="pdf-highlight"
                style={{
                  left: `${current.locator.bbox[0] * 100}%`,
                  top: `${current.locator.bbox[1] * 100}%`,
                  width: `${(current.locator.bbox[2] - current.locator.bbox[0]) * 100}%`,
                  height: `${(current.locator.bbox[3] - current.locator.bbox[1]) * 100}%`,
                }}
              />
            )}
          </div>
          <p className="muted small">
            原生文字与原始版面并排核对，来源高亮不会改动文件。
          </p>
        </div>
      )}
    </div>
  );
}
