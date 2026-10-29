import { useState } from "react";
import { Upload, ArrowUpRight, FileSearch } from "lucide-react";
import { api, post } from "../api/client";
import type { WorkspaceProps, Asset } from "../api/types";
import {
  Badge,
  Empty,
  FileIcon,
  Modal,
  label,
  size,
  time,
} from "../components/Common";

export default function MaterialsPage({
  data,
  busy,
  act,
  onAsset,
}: WorkspaceProps) {
  const [filter, setFilter] = useState("all"),
    [upload, setUpload] = useState(false),
    [kind, setKind] = useState("brief"),
    [files, setFiles] = useState<File[]>([]);
  const visible = data.assets.filter(
    (a) => filter === "all" || a.kind === filter,
  );
  const uploadFiles = () =>
    act(async () => {
      for (const file of files) {
        const form = new FormData();
        form.set("kind", kind);
        form.set("file", file);
        const a = await api<Asset>(`/projects/${data.project.id}/assets`, {
          method: "POST",
          body: form,
        });
        await post(`/assets/${a.id}/parse`);
      }
      setUpload(false);
      setFiles([]);
    }, "文件已保存，正在后台解析");
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">SOURCE MATERIALS</div>
          <h1>素材与原文</h1>
          <p>保留原始文件与来源位置，让每条审核依据可以回看。</p>
        </div>
        <button className="primary" onClick={() => setUpload(true)}>
          <Upload size={16} />
          上传素材
        </button>
      </div>
      <div className="panel">
        <div className="panel-toolbar">
          <div className="tabs">
            {["all", "brief", "script", "video", "cover", "caption"].map(
              (k) => (
                <button
                  key={k}
                  className={filter === k ? "active" : ""}
                  onClick={() => setFilter(k)}
                >
                  {k === "all" ? "全部素材" : label(k)}
                  <span>
                    {
                      data.assets.filter((a) => k === "all" || a.kind === k)
                        .length
                    }
                  </span>
                </button>
              ),
            )}
          </div>
          <span className="muted small">原件不可覆盖 · SHA-256 校验</span>
        </div>
        {visible.length ? (
          <div className="asset-table">
            <div className="asset-row table-header">
              <span>文件与类型</span>
              <span>内容信息</span>
              <span>解析状态</span>
              <span>操作</span>
            </div>
            {visible.map((a) => (
              <div className="asset-row" key={a.id}>
                <div className="file-cell">
                  <span className={`file-icon ${a.kind}`}>
                    <FileIcon kind={a.kind} />
                  </span>
                  <div>
                    <button
                      className="text-button filename"
                      onClick={() => onAsset(a)}
                    >
                      {a.filename}
                    </button>
                    <div className="muted small">
                      {label(a.kind)} · {size(a.bytes)} ·{" "}
                      {a.sha256.slice(0, 10)}
                    </div>
                  </div>
                </div>
                <div className="small">
                  {a.metadata.duration_ms
                    ? `${time(a.metadata.duration_ms)} · ${a.metadata.width} × ${a.metadata.height}`
                    : a.metadata.page_count
                      ? `${a.metadata.page_count} 页`
                      : a.coverage.find((c) => c.sheet)?.sheet || "原始文件"}
                  {a.metadata.has_audio && (
                    <span className="muted block">含原始音轨</span>
                  )}
                </div>
                <div>
                  <Badge value={a.parse_status} />
                  {a.coverage.some((c) => c.visual_review === "pending") && (
                    <div className="small muted">图中内容待核对</div>
                  )}
                </div>
                <div className="row-actions">
                  <button
                    aria-label={`查看 ${a.filename}`}
                    title="查看原文"
                    onClick={() => onAsset(a)}
                  >
                    <ArrowUpRight size={18} />
                  </button>
                  {["NOT_PARSED", "FAILED"].includes(a.parse_status) && (
                    <button
                      disabled={busy}
                      title="解析文件"
                      onClick={() =>
                        act(
                          () => post(`/assets/${a.id}/parse`),
                          "解析任务已提交",
                        )
                      }
                    >
                      <FileSearch size={17} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            title="还没有这类素材"
            description="上传 Brief、达人脚本或完整视频，开始建立审核依据。"
          />
        )}
      </div>
      <div className="footnote">
        部分解析表示仍有内容需要核对。原生文字提取成功，不代表图片和排版内的要求已完整读取。
      </div>
      {upload && (
        <Modal title="上传审核素材" onClose={() => setUpload(false)}>
          <div className="form-stack">
            <label>
              素材类型
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  setFiles([]);
                }}
              >
                {["brief", "script", "video", "cover", "caption"].map((k) => (
                  <option value={k} key={k}>
                    {label(k)}
                  </option>
                ))}
              </select>
            </label>
            <label className="upload-zone">
              <Upload size={28} />
              <strong>选择文件</strong>
              <span>
                {files.length
                  ? files.map((f) => f.name).join("、")
                  : "支持多文件；视频请上传完整 MP4，最大 200 MB"}
              </span>
              <input
                aria-label="选择上传文件"
                type="file"
                multiple
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
                accept={
                  kind === "video"
                    ? ".mp4"
                    : kind === "cover"
                      ? ".png,.jpg,.jpeg,.webp"
                      : kind === "caption"
                        ? ".txt"
                        : ".pdf,.doc,.docx,.txt,.xlsx"
                }
              />
            </label>
            <div className="notice compact">
              上传会创建新素材版本；替换达人稿件请在“达人提交”中创建修订版。
            </div>
            <div className="form-actions">
              <button onClick={() => setUpload(false)}>取消</button>
              <button
                className="primary"
                disabled={busy || !files.length}
                onClick={uploadFiles}
              >
                上传并解析 {files.length || ""}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
