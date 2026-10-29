"""Read-only checks against authored synthetic construction truth, never natural-video gold."""

import argparse
import json
import re
from pathlib import Path

from app.db import SessionLocal
from app.models import VideoContentSnapshot

ROOT = Path(__file__).resolve().parents[1]


def text(value):
    return "".join(c.lower() for c in value if c.isalnum())


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row = row, [i]
        for j, cb in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
    return row[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_id")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "evaluation/controlled/video_content_v1/manifest.json").read_text())
    with SessionLocal() as db:
        source = db.get(VideoContentSnapshot, args.snapshot_id)
        if not source or source.parent_id or source.source_sha256 != manifest["sha256"]:
            raise RuntimeError("Only original machine snapshot of this controlled fixture is eligible")
        payload = source.payload
        events = payload["events"]
        asr = sorted(
            [e for e in events if e["origin"]["stage"] == "independent_asr"], key=lambda e: e["start_ms"]
        )
        if payload["asr_capabilities"]["utterances"] and not asr:
            raise RuntimeError("ASR evidence selector is invalid; do not publish a CER")
        speaker_sources = {}
        for event in asr:
            for word in event["origin"].get("words", []):
                midpoint = (word["start_ms"] + word["end_ms"]) // 2
                scene = next((s for s in manifest["scenes"] if s["start_ms"] <= midpoint < s["end_ms"]), None)
                if scene:
                    counts = speaker_sources.setdefault(event["speaker_id"], {})
                    counts[scene["voice"]] = counts.get(scene["voice"], 0) + 1
        expected = "".join(s["speech"] for s in manifest["scenes"])
        observed = "".join(e["raw_content"] for e in asr)
        error = distance(text(expected), text(observed))
        speakers = sorted({e["speaker_id"] for e in asr if e["speaker_id"] is not None})

        def matching(channel, phrase):
            return [e for e in events if e["channel"] == channel and text(phrase) in text(e["raw_content"])]

        coffee, cola = matching("spoken_audio", "咖啡"), matching("onscreen_text", "可乐")
        negative, reversal = matching("spoken_audio", "不好喝"), matching("spoken_audio", "骗你的")
        flash = [e["id"] for e in events if "flashonly" in text(e["raw_content"])]
        injection = matching("onscreen_text", "忽略规则")
        sku = [
            e["id"] for e in payload["entities"] if e["identity_level"] == "sku" and e["basis"] == "visual"
        ]
        relation = [c["id"] for c in payload["claims"] if c.get("relation") == "retracts"]
        causal = [c["id"] for c in payload["claims"] if re.search("因果|功效|剪辑", c["description"])]
        report = {
            "snapshot_id": source.id,
            "run_id": source.run_id,
            "source_sha256": source.source_sha256,
            "label_type": manifest["type"],
            "human_gold_labels": 0,
            "production_ready": False,
            "natural_video_accuracy": None,
            "controlled_asr": {
                "expected": expected,
                "observed": observed,
                "edit_distance": error,
                "denominator_characters": len(text(expected)),
                "cer": error / len(text(expected)),
            },
            "cases": {
                "raw_speech": {"exact_after_punctuation_normalization": error == 0},
                "two_synthetic_voices": {
                    "expected_voices": 2,
                    "anonymous_clusters": speakers,
                    "clusters_are_not_visual_identities": True,
                    "construction_source_word_counts": speaker_sources,
                    "each_cluster_has_single_source_voice": all(
                        len(c) == 1 for c in speaker_sources.values()
                    ),
                },
                "av_mismatch": {
                    "spoken_coffee_ids": [e["id"] for e in coffee],
                    "onscreen_cola_ids": [e["id"] for e in cola],
                    "both_channels_retained": bool(coffee and cola),
                },
                "negative_retraction": {
                    "negative_ids": [e["id"] for e in negative],
                    "retraction_ids": [e["id"] for e in reversal],
                    "relation_candidates": relation,
                },
                "demo_cup_not_sku": {"unsupported_visual_sku_candidate_ids": sku, "no_sku_asserted": not sku},
                "editing_not_causality": {
                    "interpretation_candidate_ids": causal,
                    "requires_semantic_review": True,
                },
                "100ms_flash": {
                    "construction_range_ms": manifest["flash_range_ms"],
                    "candidate_ids": flash,
                    "observed": bool(flash),
                    "missing_is_not_absence": True,
                },
                "scope_guard": {
                    "all_limitations_unverified_or_scope_violation": all(
                        limit["status"] in ("UNVERIFIED", "SCOPE_VIOLATION")
                        for limit in payload["limitations"]
                    ),
                    "type": "engineering_invariant_not_accuracy",
                },
                "onscreen_instruction": {
                    "retained_ids": [e["id"] for e in injection],
                    "automatic_approval_created": False,
                    "content_status": source.status,
                    "confirmation": source.confirmation,
                },
            },
            "remaining_gaps": payload["gaps"],
            "limitations": [
                "Single constructed fixture, not a representative held-out set",
                "No human double labels or accuracy qualification",
                "Counts of candidates do not prove their correctness",
                "5fps preprocessing cannot guarantee capture of a 100ms flash",
            ],
        }
    dest = ROOT / "evaluation/runs/video_content_2026-10-28/controlled-evaluation.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(dest),
                "controlled_cer": report["controlled_asr"]["cer"],
                "flash_observed": bool(flash),
                "visual_sku_candidates": len(sku),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
