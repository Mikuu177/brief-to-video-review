import {
  createContext,
  useContext,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import type { Config } from "../api/types";
import {
  X,
  FileText,
  Film,
  Image,
  FileSpreadsheet,
  LoaderCircle,
} from "lucide-react";

export const ActionErrorContext = createContext("");
export function awaitsAudioVerification(status?: Config["video"]) {
  return (
    !status?.ready &&
    status?.missing.length === 1 &&
    status.missing[0] === "ARK_VIDEO_AUDIO_VERIFIED"
  );
}
const labels: Record<string, string> = {
  COMPLETED: "已完成",
  PARTIAL: "部分解析",
  success: "文字已提取",
  partial: "部分解析",
  blank: "空白页",
  needs_ocr: "待视觉解析",
  NOT_PARSED: "待解析",
  QUEUED: "排队中",
  RUNNING: "进行中",
  INCOMPLETE: "未完整完成",
  FAILED: "失败",
  CANCELED: "已取消",
  DRAFT: "草案",
  FROZEN: "已冻结",
  CURRENT: "当前版本",
  STALE: "版本已过期",
  NOT_READY: "尚不可判断",
  NEEDS_REVISION: "建议修改",
  NEEDS_HUMAN: "需人工核对",
  AI_PASSED: "AI 初审通过",
  PASS: "符合",
  FAIL: "有问题",
  UNCERTAIN: "待核对",
  NOT_APPLICABLE: "不适用",
  pending: "待确认",
  confirmed: "已确认",
  excluded: "已排除",
  APPROVE: "人工通过",
  REQUEST_CHANGES: "退回修改",
  REJECT: "拒绝",
  source_reading: "来源读取",
  video_content: "视频内容读取",
  script: "脚本",
  video: "成片",
  brief: "Brief",
  cover: "封面",
  caption: "发布文案",
  image_post: "图文",
  xiaohongshu: "小红书",
  douyin: "抖音",
  bilibili: "B站",
  unconfirmed: "未确认",
  launch: "上市种草期",
  preheat: "预热期",
  spoken_audio: "口播声音",
  onscreen_text: "画面文字",
  visual_event: "画面事件",
  metadata: "文本 / 元数据",
  blocker: "阻断",
  major: "重要",
  minor: "一般",
  advisory: "建议",
  unclassified_audio: "未分类声音",
  must: "强制要求",
  should: "建议要求",
  fact: "产品事实",
  example: "创意示例",
  exception: "例外",
  out_of_scope: "不适用项",
};
export const label = (key: string) => labels[key] || key;
export function Badge({
  value,
  children,
}: {
  value: string;
  children?: ReactNode;
}) {
  return (
    <span className={`badge tone-${value.toLowerCase()}`}>
      {children || label(value)}
    </span>
  );
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <FileText size={25} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const actionError = useContext(ActionErrorContext);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const dialog = dialogRef.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const isTopDialog = () => {
      const dialogs = document.querySelectorAll(
        '[role="dialog"][aria-modal="true"]',
      );
      return dialogs[dialogs.length - 1] === dialog;
    };
    const controls = () =>
      Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.getClientRects().length && !el.closest("[inert]"));
    const focusFirst = () => (controls()[0] || dialog).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopDialog()) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      } else if (event.key === "Tab") {
        const available = controls();
        const first = available[0],
          last = available[available.length - 1];
        if (!first) {
          event.preventDefault();
          dialog.focus();
        } else if (
          event.shiftKey &&
          (document.activeElement === first ||
            !dialog.contains(document.activeElement))
        ) {
          event.preventDefault();
          last.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last ||
            !dialog.contains(document.activeElement))
        ) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (isTopDialog() && !dialog.contains(event.target as Node)) focusFirst();
    };
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusin", onFocusIn);
    focusFirst();
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusin", onFocusIn);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`modal ${wide ? "wide" : ""}`}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          <button className="icon-button" aria-label="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        {actionError && (
          <div className="notice danger" role="alert">
            {actionError}
          </div>
        )}
        {children}
      </section>
    </div>
  );
}
export function FileIcon({ kind }: { kind: string }) {
  const Icon =
    kind === "video"
      ? Film
      : kind === "cover"
        ? Image
        : kind === "script"
          ? FileSpreadsheet
          : FileText;
  return <Icon size={22} />;
}
export function Loading() {
  return (
    <div className="loading">
      <LoaderCircle className="spin" size={20} />
      正在读取…
    </div>
  );
}
export const time = (ms: number | null) =>
  ms === null
    ? "—"
    : `${Math.floor(ms / 60000)
        .toString()
        .padStart(
          2,
          "0",
        )}:${((ms % 60000) / 1000).toFixed(1).padStart(4, "0")}`;
export const size = (bytes: number) =>
  bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.round(bytes / 1024)} KB`;
export const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
