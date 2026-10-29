import html
import json


def render_report(data: dict) -> str:
    def escape(value):
        return html.escape(str(value))

    def evidence_time(item):
        if item["start_ms"] is None or item["end_ms"] is None:
            return "无视频时间码"
        return f"{item['start_ms']}–{item['end_ms']} ms"

    rules = {r["id"]: r for r in data["rules"]}
    rule_types = {"must": "强制要求", "should": "建议要求"}
    rows = "".join(
        f"<tr><td>{escape(rules.get(c['rule_id'], {}).get('title', c['rule_id']))}</td>"
        f"<td>{escape(rule_types.get(rules.get(c['rule_id'], {}).get('type'), '其他'))}</td>"
        f"<td>{escape(c['status'])}</td><td>{escape(c['explanation'])}</td>"
        f"<td>{escape(c['suggestion'])}</td><td>{escape('；'.join(c.get('limitations', [])))}</td></tr>"
        for c in data["checks"]
    )
    evidence = "".join(
        f"<li>{escape(e['channel'])} · {escape(evidence_time(e))}"
        f"<p>{escape(e['content'])}</p></li>"
        for e in data["evidence"]
    )
    decisions = "".join(
        f"<li>{escape(d['actor'])} · {escape(d['decision'])} · {escape(d['mode'])}"
        f"<p>{escape(d['reason'])}</p></li>"
        for d in data["decisions"]
    )
    assets = "".join(
        f"<li>{escape(a['kind'])}：{escape(a['filename'])}<br><code>{escape(a['sha256'])}</code></li>"
        for a in data["assets"]
    )
    confirmation = {"experimental": "技术试验规范", "brand_confirmed": "品牌确认规范"}.get(
        data.get("ruleset_confirmation_kind"), "尚未确认规范"
    )
    trial_limits = "".join(
        f"<li>{escape(limit)}</li>" for limit in data["input_snapshot"].get("trial_limits", [])
    )
    legacy_context = (
        data.get("kind") == "video"
        and data["input_snapshot"].get("mode") != "independent_human"
        and data["input_snapshot"].get("verification_context_version", 0) < 3
    )
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>内容审核报告</title>
    <style>body{{font:15px/1.7 system-ui;max-width:1000px;margin:40px auto;color:#20312e;padding:20px}}
    table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:10px;text-align:left}}
    code{{word-break:break-all}}pre{{white-space:pre-wrap;word-break:break-word}}@media print{{body{{margin:0}}}}</style>
    <h1>达人内容审核报告</h1><p>运行 {escape(data["id"])} · {escape(data["created_at"])}</p>
    <p>执行：{escape(data["status"])}　AI 建议：{escape(data["assessment"])}　版本：{escape(data["freshness"])}</p>
    <p>审核方式：{escape(data["input_snapshot"].get("mode", "未执行 AI"))}</p>
    <p>规范身份：{escape(confirmation)}　成片模式：{"历史试审" if data["input_snapshot"].get("historical_trial") else "依输入版本记录"}</p>
    {"<p><strong>旧版复核上下文记录</strong>：此记录未使用新版品牌快照与产品事实复核。竞品相关解释需重新核对，不能将主推品牌当作第三方；历史结论与证据原样保留。</p>" if legacy_context else ""}
    {"<p><strong>受限技术试审，不能自动通过</strong></p><ul>" + trial_limits + "</ul>" if trial_limits else ""}
    <p>输入哈希：<code>{escape(data["input_hash"])}</code></p>
    <h2>素材版本</h2><ul>{assets}</ul><h2>逐条检查</h2>
    <table><tr><th>规则</th><th>要求类型</th><th>结论</th><th>依据</th><th>建议</th><th>限制</th></tr>{rows}</table>
    <h2>证据</h2><ul>{evidence}</ul><h2>人工决定</h2><ul>{decisions or "<li>尚未人工确认</li>"}</ul>
    <h2>限制与执行结果</h2><pre>{escape(json.dumps(data["result"], ensure_ascii=False, indent=2))}</pre>
    <h2>规则与出处</h2><pre>{escape(json.dumps(data["rules"], ensure_ascii=False, indent=2))}</pre></html>"""
