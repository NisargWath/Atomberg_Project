from datetime import datetime, timezone


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def calculate_progress(goal, achievement):
    metric_type = goal.get("metric_type", "min")
    target = float(goal.get("target") or 0)

    if metric_type == "zero_based":
        return 100 if float(achievement or 0) == 0 else 0

    if metric_type == "timeline":
        deadline = parse_date(goal.get("deadline"))
        completed_at = parse_date(goal.get("completed_at"))
        if not deadline:
            return 0
        if completed_at and completed_at <= deadline:
            return 100
        if completed_at:
            return 0
        created_at = parse_date(goal.get("created_at")) or datetime.now(timezone.utc)
        total = max((deadline - created_at).total_seconds(), 1)
        elapsed = min(max((datetime.now(timezone.utc) - created_at).total_seconds(), 0), total)
        return round((elapsed / total) * 100, 2)

    actual = float(achievement or 0)
    if target <= 0 or actual < 0:
        return 0

    if metric_type == "max":
        if actual == 0:
            return 100
        return round(min((target / actual) * 100, 100), 2)

    return round(min((actual / target) * 100, 100), 2)
