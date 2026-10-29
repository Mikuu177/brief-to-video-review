"""Parser-owned IDs; copied input IDs become separate, explicitly ambiguous candidates."""

from app.errors import ReviewError


def normalize_identifiers(body, namespace, known_events, known_entities):
    all_ids = [
        item.get("id")
        for field in ("events", "entities", "claims")
        for item in body.get(field, [])
        if isinstance(item, dict)
    ]
    strings = [key for key in all_ids if isinstance(key, str)]
    if len(strings) != len(set(strings)):
        raise ReviewError("CONTENT_IDS_INVALID", "响应内ID重复，不能猜测引用对象")
    aliases = {field: {} for field in ("events", "entities", "claims")}
    metadata = {}
    used = set(strings)
    for field, tag in [("events", "E"), ("entities", "P"), ("claims", "C")]:
        for index, item in enumerate(body.get(field, [])):
            if not isinstance(item, dict):
                continue
            key = item.get("id")
            if not isinstance(key, str) or not key or key.startswith(namespace + "_"):
                continue
            canonical = f"{namespace}_PARSER_{tag}{index + 1:04}"
            if canonical in used:
                raise ReviewError("CONTENT_IDS_INVALID", "解析器命名空间与已有ID冲突")
            used.add(canonical)
            aliases[field][key] = canonical
            item["id"] = canonical
            metadata[canonical] = {"reported_id": key, "canonical_id": canonical}

    def uncertain(item, field):
        allowed = (
            {"CANDIDATE", "UNCLEAR", "NOT_OBSERVED"}
            if field == "events"
            else ({"CANDIDATE", "UNKNOWN"} if field == "entities" else {"CANDIDATE", "UNKNOWN", "DISPUTED"})
        )
        if item.get("status", "CANDIDATE") in allowed and item.get("status") != "NOT_OBSERVED":
            item["status"] = "UNCLEAR" if field == "events" else "UNKNOWN"

    entity_labels = known_entities | set(aliases["entities"]) | {
        item["id"]
        for item in body.get("entities", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for field in ("events", "entities", "claims"):
        for item in body.get(field, []):
            if not isinstance(item, dict):
                continue
            changes = []
            if field == "events":
                for role in ("actor_id", "object_id"):
                    value = item.get(role)
                    reported = value
                    trailing_comma = (
                        isinstance(value, str)
                        and value not in entity_labels
                        and value.endswith(",")
                        and value[:-1] in entity_labels
                    )
                    if trailing_comma:
                        value = value[:-1]
                    if isinstance(value, str) and value in aliases["entities"]:
                        replacement = aliases["entities"][value]
                        candidates = [value, replacement] if value in known_entities else [replacement]
                        item[role] = None if len(candidates) > 1 else replacement
                        changes.append({"field": role, "reported": value, "candidates": candidates})
                        if len(candidates) > 1:
                            uncertain(item, field)
                    elif trailing_comma:
                        item[role] = value
                        changes.append({"field": role, "reported": reported, "candidates": [value]})
                    if trailing_comma:
                        changes[-1].update(reported=reported, normalization="single_trailing_comma")
                        uncertain(item, field)
            else:
                references = item.get("evidence_ids", [])
                if isinstance(references, list) and all(isinstance(key, str) for key in references):
                    resolved = []
                    for key in references:
                        replacement = aliases["events"].get(key)
                        if replacement:
                            candidates = [key, replacement] if key in known_events else [replacement]
                            changes.append(
                                {"field": "evidence_ids", "reported": key, "candidates": candidates}
                            )
                            resolved.extend(candidates)
                            if len(candidates) > 1:
                                uncertain(item, field)
                        else:
                            resolved.append(key)
                    item["evidence_ids"] = list(dict.fromkeys(resolved))
            if changes:
                metadata.setdefault(item.get("id"), {})["reference_candidates"] = changes
    return metadata
