from app.errors import ReviewError


def windows(duration_ms: int, window_ms=45000, overlap_ms=5000) -> list[tuple[int, int]]:
    if duration_ms <= 0 or overlap_ms < 0 or window_ms <= overlap_ms:
        raise ValueError("Invalid window configuration")
    result, start = [], 0
    while start < duration_ms:
        end = min(start + window_ms, duration_ms)
        result.append((start, end))
        if end == duration_ms:
            break
        start = end - overlap_ms
    return result


def coverage_complete(intervals: list[tuple[int, int]], duration_ms: int) -> bool:
    cursor = 0
    for start, end in sorted(intervals):
        if start > cursor or end <= start:
            return False
        cursor = max(cursor, end)
    return cursor >= duration_ms


def global_range(
    start_ms: int, end_ms: int, mapping: dict, duration_ms: int, endpoint_rounding_ms: int = 0
) -> tuple[int, int]:
    local_duration = mapping.get("actual_end_ms", duration_ms) - mapping["actual_start_ms"]
    if not 0 <= start_ms < local_duration or not start_ms < end_ms <= local_duration + endpoint_rounding_ms:
        raise ReviewError("MODEL_RESPONSE_INVALID", "视频证据时间超出当前连续片段范围")
    end_ms = min(end_ms, local_duration)
    start = mapping["actual_start_ms"] + start_ms
    end = mapping["actual_start_ms"] + end_ms
    if not 0 <= start < end <= duration_ms + mapping.get("tolerance_ms", 0):
        raise ReviewError("MODEL_RESPONSE_INVALID", "视频证据时间超出原片范围")
    return start, min(end, duration_ms)


def threshold_uncertain(time_ms: int, uncertainty_ms: int | None, threshold_ms: int) -> bool:
    return uncertainty_ms is None or time_ms - uncertainty_ms <= threshold_ms <= time_ms + uncertainty_ms
