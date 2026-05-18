from uuid import uuid4

from flask import Blueprint, g, request

from middleware.auth import auth_required
from models.store import clean_doc, now_iso
from services.analytics import dashboard_summary
from services.audit import write_audit
from services.progress import calculate_progress
from services.validators import ALLOWED_STATUS, validate_goal_payload, validate_sheet_for_submission
from utils.responses import error, ok, respond

goals_bp = Blueprint("goals", __name__)


def serialize_many(rows):
    return [clean_doc(row) for row in rows]


def current_employee_id():
    return g.current_user["id"]


@goals_bp.get("")
@auth_required("employee", "manager", "admin")
def list_goals():
    user = g.current_user
    query = {}
    if user["role"] == "employee":
        query["employee_id"] = user["id"]
    elif request.args.get("employee_id"):
        query["employee_id"] = request.args["employee_id"]
    return respond(*ok({"goals": serialize_many(g.db.goals.find(query))}))


@goals_bp.get("/dashboard")
@auth_required("employee")
def employee_dashboard():
    goals = serialize_many(g.db.goals.find({"employee_id": current_employee_id()}))
    updates = serialize_many(g.db.quarterly_updates.find({"employee_id": current_employee_id()}))
    users = serialize_many(g.db.users.find({}))
    logs = serialize_many(g.db.audit_logs.find({"actor_id": current_employee_id()}))
    return respond(*ok(dashboard_summary(goals, updates, users, logs)))


@goals_bp.post("")
@auth_required("employee", "manager", "admin")
def create_goal():
    payload = request.get_json() or {}
    user = g.current_user
    employee_id = payload.get("employee_id") if user["role"] in {"manager", "admin"} else user["id"]
    if not employee_id:
        return respond(*error("employee_id is required"))

    existing = g.db.goals.find({"employee_id": employee_id})
    if len(existing) >= 8:
        return respond(*error("Maximum goals per employee is 8"))

    errors = validate_goal_payload(payload)
    if errors:
        return respond(*error("Goal validation failed", 422, errors))

    is_shared = bool(payload.get("is_shared")) and user["role"] in {"manager", "admin"}
    doc = {
        "_id": uuid4().hex,
        "employee_id": employee_id,
        "manager_id": payload.get("manager_id") or g.db.users.find_one({"_id": employee_id}).get("manager_id"),
        "title": payload["title"],
        "description": payload["description"],
        "thrust_area": payload["thrust_area"],
        "uom": payload["uom"],
        "metric_type": payload.get("metric_type") or ("timeline" if payload["uom"] == "timeline" else "min"),
        "target": payload["target"],
        "deadline": payload.get("deadline"),
        "weightage": float(payload["weightage"]),
        "sheet_status": "draft",
        "approval_status": "draft",
        "locked": False,
        "is_shared": is_shared,
        "shared_group_id": payload.get("shared_group_id"),
        "created_by": user["id"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    result = g.db.goals.insert_one(doc)
    write_audit(g.db, user["id"], "CREATE_GOAL", "goal", str(result.inserted_id), {"shared": is_shared})
    doc["_id"] = result.inserted_id
    return respond(*ok({"goal": clean_doc(doc)}, "Goal created", 201))


@goals_bp.put("/<goal_id>")
@auth_required("employee", "manager", "admin")
def update_goal(goal_id):
    goal = clean_doc(g.db.goals.find_one({"_id": goal_id}))
    if not goal:
        return respond(*error("Goal not found", 404))
    user = g.current_user
    payload = request.get_json() or {}

    is_owner = user["role"] == "employee" and goal["employee_id"] == user["id"]
    can_manage = user["role"] in {"manager", "admin"}
    if not (is_owner or can_manage):
        return respond(*error("Access denied", 403))
    if goal.get("locked") and user["role"] != "admin":
        return respond(*error("Approved goals are locked. Ask Admin/HR to unlock."))

    errors = validate_goal_payload(payload, partial=True)
    if errors:
        return respond(*error("Goal validation failed", 422, errors))

    allowed = {"title", "description", "thrust_area", "uom", "metric_type", "target", "deadline", "weightage"}
    if goal.get("is_shared") and is_owner:
        allowed = {"weightage"}
    updates = {key: payload[key] for key in allowed if key in payload}
    if "weightage" in updates:
        updates["weightage"] = float(updates["weightage"])
    updates["updated_at"] = now_iso()
    g.db.goals.update_one({"_id": goal_id}, {"$set": updates})
    write_audit(g.db, user["id"], "UPDATE_GOAL", "goal", goal_id, previous_value=goal, updated_value=updates)
    return respond(*ok({"goal": clean_doc(g.db.goals.find_one({"_id": goal_id}))}, "Goal updated"))


@goals_bp.delete("/<goal_id>")
@auth_required("employee", "admin")
def delete_goal(goal_id):
    goal = clean_doc(g.db.goals.find_one({"_id": goal_id}))
    if not goal:
        return respond(*error("Goal not found", 404))
    if g.current_user["role"] == "employee" and goal["employee_id"] != g.current_user["id"]:
        return respond(*error("Access denied", 403))
    if goal.get("locked"):
        return respond(*error("Locked goals cannot be deleted"))
    g.db.goals.delete_one({"_id": goal_id})
    write_audit(g.db, g.current_user["id"], "DELETE_GOAL", "goal", goal_id)
    return respond(*ok(message="Goal deleted"))


@goals_bp.post("/submit")
@auth_required("employee")
def submit_goals():
    goals = serialize_many(g.db.goals.find({"employee_id": current_employee_id()}))
    submit_candidates = [goal for goal in goals if goal["approval_status"] in {"draft", "rework"}]
    active_goals = [goal for goal in goals if goal["approval_status"] not in {"rejected"}]
    errors = validate_sheet_for_submission(active_goals)
    if errors:
        return respond(*error("Cannot submit goals", 422, errors))
    if not submit_candidates:
        return respond(*error("No draft or rework goals available to submit", 422))
    ids = [goal["id"] for goal in submit_candidates]
    for goal_id in ids:
        g.db.goals.update_one({"_id": goal_id}, {"$set": {"sheet_status": "submitted", "approval_status": "submitted", "updated_at": now_iso()}})
    sheet = {
        "_id": uuid4().hex,
        "employee_id": current_employee_id(),
        "manager_id": g.current_user.get("manager_id"),
        "goal_ids": ids,
        "status": "submitted",
        "submitted_at": now_iso(),
    }
    result = g.db.goal_sheets.insert_one(sheet)
    write_audit(g.db, current_employee_id(), "SUBMIT_GOAL_SHEET", "goal_sheet", str(result.inserted_id), {"goal_count": len(ids)})
    return respond(*ok({"sheet_id": str(result.inserted_id)}, "Goals submitted for manager approval"))


@goals_bp.post("/<goal_id>/quarterly-updates")
@auth_required("employee")
def add_quarterly_update(goal_id):
    goal = clean_doc(g.db.goals.find_one({"_id": goal_id, "employee_id": current_employee_id()}))
    if not goal:
        return respond(*error("Goal not found", 404))
    if not goal.get("locked"):
        return respond(*error("Quarterly updates are available after manager approval"))
    payload = request.get_json() or {}
    status = payload.get("status", "on_track")
    if status not in ALLOWED_STATUS:
        return respond(*error("Invalid status"))
    achievement = payload.get("actual_achievement", 0)
    progress = calculate_progress(goal, achievement)
    doc = {
        "_id": uuid4().hex,
        "goal_id": goal_id,
        "employee_id": current_employee_id(),
        "quarter": payload.get("quarter", "Q1"),
        "actual_achievement": achievement,
        "progress": progress,
        "status": status,
        "notes": payload.get("notes", ""),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "manager_reviewed": False,
    }
    result = g.db.quarterly_updates.insert_one(doc)
    write_audit(g.db, current_employee_id(), "ADD_QUARTERLY_UPDATE", "quarterly_update", str(result.inserted_id), {"progress": progress})
    doc["_id"] = result.inserted_id
    return respond(*ok({"update": clean_doc(doc)}, "Quarterly update saved", 201))


@goals_bp.get("/quarterly-updates")
@auth_required("employee", "manager", "admin")
def list_quarterly_updates():
    query = {}
    if g.current_user["role"] == "employee":
        query["employee_id"] = g.current_user["id"]
    elif request.args.get("employee_id"):
        query["employee_id"] = request.args["employee_id"]
    return respond(*ok({"updates": serialize_many(g.db.quarterly_updates.find(query))}))
