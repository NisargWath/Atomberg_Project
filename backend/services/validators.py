ALLOWED_UOM = {"numeric", "percentage", "timeline", "zero_based"}
ALLOWED_METRIC_TYPES = {"min", "max", "timeline", "zero_based"}
ALLOWED_STATUS = {"not_started", "on_track", "completed"}


def validate_goal_payload(payload, partial=False):
    errors = {}
    required = ["title", "description", "thrust_area", "uom", "target", "weightage"]
    if not partial:
        for field in required:
            if payload.get(field) in (None, ""):
                errors[field] = "Required"

    if "uom" in payload and payload.get("uom") not in ALLOWED_UOM:
        errors["uom"] = "Invalid unit of measurement"
    if "metric_type" in payload and payload.get("metric_type") not in ALLOWED_METRIC_TYPES:
        errors["metric_type"] = "Invalid metric type"

    if "weightage" in payload:
        try:
            weightage = float(payload.get("weightage"))
            if weightage < 10:
                errors["weightage"] = "Minimum weightage is 10%"
            if weightage > 100:
                errors["weightage"] = "Weightage cannot exceed 100%"
        except (TypeError, ValueError):
            errors["weightage"] = "Weightage must be numeric"

    return errors


def validate_sheet_for_submission(goals):
    if not goals:
        return {"goals": "Create at least one goal before submitting"}
    if len(goals) > 8:
        return {"goals": "Maximum 8 goals are allowed"}
    total = sum(float(goal.get("weightage", 0)) for goal in goals)
    if round(total, 2) != 100:
        return {"weightage": "Total weightage across goals must equal 100%"}
    low = [goal.get("title", "Goal") for goal in goals if float(goal.get("weightage", 0)) < 10]
    if low:
        return {"weightage": "Every goal must have at least 10% weightage"}
    return {}
