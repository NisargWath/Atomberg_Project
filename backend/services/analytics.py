from collections import Counter


def public_users(users):
    safe = []
    for user in users:
        item = dict(user)
        item.pop("password_hash", None)
        safe.append(item)
    return safe


def user_maps(users):
    by_id = {user["id"]: user for user in users}
    return by_id


def latest_updates_by_goal(updates):
    latest = {}
    for update in sorted(updates, key=lambda item: item.get("updated_at") or item.get("created_at") or ""):
        latest[update["goal_id"]] = update
    return latest


def average(values):
    values = [float(value or 0) for value in values]
    if not values:
        return 0
    return round(sum(values) / len(values), 2)


def goal_progress(goal, latest_updates):
    update = latest_updates.get(goal["id"])
    return float(update.get("progress", 0)) if update else 0


def build_goal_rows(goals, updates, users):
    by_user = user_maps(users)
    latest_updates = latest_updates_by_goal(updates)
    rows = []
    for goal in goals:
        employee = by_user.get(goal.get("employee_id"), {})
        manager = by_user.get(goal.get("manager_id"), {})
        latest = latest_updates.get(goal["id"], {})
        rows.append({
            **goal,
            "employee_name": employee.get("name", goal.get("employee_id")),
            "manager_name": manager.get("name", goal.get("manager_id")),
            "department": employee.get("department", ""),
            "latest_quarter": latest.get("quarter"),
            "latest_progress": float(latest.get("progress", 0) or 0),
            "latest_status": latest.get("status"),
            "latest_actual": latest.get("actual_achievement"),
        })
    return rows


def dashboard_summary(goals, updates, users, audit_logs=None):
    audit_logs = audit_logs or []
    latest_updates = latest_updates_by_goal(updates)
    status_counts = Counter(goal.get("approval_status", "draft") for goal in goals)
    update_status_counts = Counter(update.get("status", "not_started") for update in updates)
    progress_values = [goal_progress(goal, latest_updates) for goal in goals if goal.get("approval_status") == "approved"]
    employee_ids = {goal.get("employee_id") for goal in goals if goal.get("employee_id")}

    goals_by_status = [{"name": key.replace("_", " ").title(), "value": value} for key, value in status_counts.items()]
    goals_by_thrust = [{"name": key, "value": value} for key, value in Counter(goal.get("thrust_area", "Other") for goal in goals).items()]
    quarterly_progress = []
    for quarter in ["Q1", "Q2", "Q3", "Q4"]:
        quarter_updates = [update for update in updates if update.get("quarter") == quarter]
        quarterly_progress.append({
            "quarter": quarter,
            "progress": average([update.get("progress", 0) for update in quarter_updates]),
            "updates": len(quarter_updates),
        })

    by_user = user_maps(users)
    employee_progress = []
    for employee_id in sorted(employee_ids):
        employee_goals = [goal for goal in goals if goal.get("employee_id") == employee_id]
        employee_progress.append({
            "employee_id": employee_id,
            "name": by_user.get(employee_id, {}).get("name", employee_id),
            "department": by_user.get(employee_id, {}).get("department", ""),
            "progress": average([goal_progress(goal, latest_updates) for goal in employee_goals]),
            "goals": len(employee_goals),
            "approved": len([goal for goal in employee_goals if goal.get("approval_status") == "approved"]),
        })

    recent_updates = sorted(updates, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)[:8]
    recent_activity = sorted(audit_logs, key=lambda item: item.get("created_at", ""), reverse=True)[:8]

    stats = {
        "total_goals": len(goals),
        "approved_goals": status_counts.get("approved", 0),
        "pending_approvals": status_counts.get("submitted", 0),
        "draft_goals": status_counts.get("draft", 0),
        "rework_goals": status_counts.get("rework", 0),
        "rejected_goals": status_counts.get("rejected", 0),
        "average_progress": average(progress_values),
        "team_members": len(employee_ids),
        "employees": len([user for user in users if user.get("role") == "employee"]),
        "managers": len([user for user in users if user.get("role") == "manager"]),
        "active_users": len(users),
        "quarterly_updates": len(updates),
        "completed_updates": update_status_counts.get("completed", 0),
        "pending_reviews": len([update for update in updates if not update.get("manager_reviewed")]),
        "audit_events": len(audit_logs),
    }

    return {
        "stats": stats,
        "chart_data": {
            "goals_by_status": goals_by_status,
            "goals_by_thrust_area": goals_by_thrust,
            "quarterly_progress": quarterly_progress,
            "employee_progress": employee_progress,
            "update_status": [{"name": key.replace("_", " ").title(), "value": value} for key, value in update_status_counts.items()],
        },
        "recent_updates": recent_updates,
        "recent_activity": recent_activity,
        "goal_rows": build_goal_rows(goals, updates, users),
    }


def enrich_audit_logs(logs, users):
    by_user = user_maps(users)
    enriched = []
    for log in logs:
        metadata = log.get("metadata") or {}
        actor = by_user.get(log.get("actor_id"), {})
        enriched.append({
            **log,
            "actor_name": actor.get("name", log.get("actor_id")),
            "actor_role": actor.get("role", ""),
            "previous_value": metadata.get("previous_value", metadata.get("before", "")),
            "updated_value": metadata.get("updated_value", metadata.get("after", metadata)),
        })
    return enriched
