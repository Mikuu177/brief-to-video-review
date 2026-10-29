export interface ScriptUnit {
  sheet?: string;
  row?: number;
  paragraph?: number;
  text?: string;
  time_text: string | null;
  scene?: string | null;
  visual?: string | null;
  dialogue?: string | null;
  notes?: string | null;
  reference?: string | null;
  start_ms: null;
  end_ms: null;
  source_ids: string[];
}
export interface ScriptStructure {
  units: ScriptUnit[];
  method: string;
  limits: string[];
}
export function ScriptViewer({
  structure,
  onSource,
}: {
  structure: ScriptStructure;
  onSource: (id: string) => void;
}) {
  return (
    <details className="script-structure" open>
      <summary>
        脚本结构 <span>{structure.units.length} 个来源单元</span>
      </summary>
      {structure.units.map((unit, index) => (
        <div className="script-unit" key={index}>
          <div className="section-line">
            <strong>
              {unit.sheet
                ? `${unit.sheet} · 第${unit.row}行`
                : `段落 ${unit.paragraph}`}
            </strong>
            <span className="small muted">
              {unit.time_text || "未标注时间"}
            </span>
          </div>
          {unit.scene && (
            <p>
              <b>场景</b>
              {unit.scene}
            </p>
          )}
          {unit.visual && (
            <p>
              <b>画面</b>
              {unit.visual}
            </p>
          )}
          {unit.dialogue && (
            <p>
              <b>台词 / 画外音</b>
              {unit.dialogue}
            </p>
          )}
          {unit.text && <p>{unit.text}</p>}
          {unit.notes && (
            <p>
              <b>备注</b>
              {unit.notes}
            </p>
          )}
          <button
            className="text-button small"
            onClick={() => onSource(unit.source_ids[0])}
          >
            定位原始单元格 / 段落 ↗
          </button>
        </div>
      ))}
    </details>
  );
}
