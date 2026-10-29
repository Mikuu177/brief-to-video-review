import { useEffect, useRef, useState } from "react";
import { Repeat2 } from "lucide-react";
import { mediaUrl } from "../api/client";
import { time } from "./Common";
export function VideoPlayer({
  assetId,
  selection,
  duration = 0,
  onTime,
}: {
  assetId: string;
  selection: { start: number; end: number; key: number } | null;
  duration?: number;
  onTime?: (ms: number) => void;
}) {
  const ref = useRef<HTMLVideoElement>(null),
    [loop, setLoop] = useState(false),
    [current, setCurrent] = useState(0);
  useEffect(() => {
    if (ref.current && selection) {
      ref.current.currentTime = selection.start / 1000;
      void ref.current.play().catch(() => {});
    }
  }, [selection]);
  function restartLoop(resume: boolean) {
    const video = ref.current;
    if (!loop || !selection || !video) return;
    video.currentTime = selection.start / 1000;
    if (resume) void video.play().catch(() => {});
  }
  return (
    <div className="video-player">
      <div className="video-stage">
        <video
          ref={ref}
          controls
          preload="metadata"
          src={mediaUrl(assetId)}
          onTimeUpdate={() => {
            const ms = Math.round((ref.current?.currentTime || 0) * 1000);
            setCurrent(ms);
            onTime?.(ms);
            if (loop && selection && ms >= selection.end && ref.current) {
              const resume = !ref.current.paused || ref.current.ended;
              restartLoop(resume);
            }
          }}
          onEnded={() => {
            if (ref.current?.ended) restartLoop(true);
          }}
        />
      </div>
      <div className="video-controls">
        <span>
          原片 {time(current)} <span className="muted">/ {time(duration)}</span>
        </span>
        <button
          className={loop ? "active-link" : ""}
          aria-pressed={loop}
          disabled={!selection}
          onClick={() => setLoop(!loop)}
        >
          <Repeat2 size={15} />
          证据区间循环
        </button>
      </div>
      {selection && (
        <div className="selection-label">
          定位区间 {time(selection.start)} – {time(selection.end)} ·
          使用原片时间轴
        </div>
      )}
    </div>
  );
}
