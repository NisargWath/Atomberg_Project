from uuid import uuid4

from flask import Blueprint, g, request

from middleware.auth import auth_required
from models.store import clean_doc, now_iso
from services.analytics import dashboard_summary
from services.audit import write_audit
from services.validators import validate_sheet_for_submission
from utils.responses import error, ok, respond

manager_bp = Blueprint("manager", __name__)


def rows(collection, query=None):
    return [clean_doc(row) for row in collection.find(query or {})]


@manager_bp.get("/submissions")
@auth_required("manager", "admin")
def submissions():
    query = {"approval_status": "submitted"}
    if g.current_user["role"] == "manager":
        query["manager_id"] = g.current_user["id"]
    return respond(*ok({"submissions": rows(g.db.goals, query)}))


@manager_bp.post("/goals/<goal_id>/decision")
@auth_required("manager", "admin")
def decide_goal(goal_id):
    goal = clean_doc(g.db.goals.find_one({"_id": goal_id}))
    if not goal:
        return respond(*error("Goal not found", 404))
    if g.current_user["role"] == "manager" and goal.get("manager_id") != g.current_user["id"]:
        return respond(*error("Access denied", 403))

    payload = request.get_json() or {}
    decision = payload.get("decision")
    if decision not in {"approved", "rejected", "rework"}:
        return respond(*error("Decision must be approved, rejected, or rework"))

    updates = {
        "approval_status": decision,
        "sheet_status": decision,
        "locked": decision == "approved",
        "manager_comment": payload.get("comment", ""),
        "updated_at": now_iso(),
    }
    if "target" in payload:
        updates["target"] = payload["target"]
    if "weightage" in payload:
        updates["weightage"] = float(payload["weightage"])
    g.db.goals.update_one({"_id": goal_id}, {"$set": updates})
    write_audit(g.db, g.current_user["id"], f"GOAL_{decision.upper()}", "goal", goal_id, previous_value=goal, updated_value=updates)
    return respond(*ok({"goal": clean_doc(g.db.goals.find_one({"_id": goal_id}))}, f"Goal {decision}"))


@manager_bp.post("/employees/<employee_id>/approve-sheet")
@auth_required("manager", "admin")
def approve_sheet(employee_id):
    query = {"employee_id": employee_id, "approval_status": "submitted"}
    if g.current_user["role"] == "manager":
        query["manager_id"] = g.current_user["id"]
    goals = rows(g.db.goals, query)
    errors = validate_sheet_for_submission(goals)
    if errors:
        return respond(*error("Cannot approve sheet", 422, errors))
    for goal in goals:
        g.db.goals.update_one({"_id": goal["id"]}, {"$set": {"approval_status": "approved", "sheet_status": "approved", "locked": True, "updated_at": now_iso()}})
    write_audit(g.db, g.current_user["id"], "APPROVE_GOAL_SHEET", "user", employee_id, {"goal_count": len(goals)})
    return respond(*ok(message="Goal sheet approved and locked"))


@manager_bp.post("/shared-goals")
@auth_required("manager", "admin")
def assign_shared_goal():
    payload = request.get_json() or {}
    employee_ids = payload.get("employee_ids") or []
    required = ["title", "description", "thrust_area", "uom", "target", "weightage"]
    missing = [field for field in required if payload.get(field) in (None, "")]
    if missing or not employee_ids:
        return respond(*error("Shared goal payload is incomplete", 422, {"missing": missing, "employee_ids": "Required"}))
    shared_group_id = f"shared-{uuid4().hex}"
    created = []
    for employee_id in employee_ids:
        employee = g.db.users.find_one({"_id": employee_id})
        if not employee:
            continue
        doc = {
            "_id": uuid4().hex,
            "employee_id": employee_id,
            "manager_id": employee.get("manager_id") or (g.current_user["id"] if g.current_user["role"] == "manager" else None),
            "title": payload["title"],
            "description": payload["description"],
            "thrust_area": payload["thrust_area"],
            "uom": payload["uom"],
            "metric_type": payload.get("metric_type", "min"),
            "target": payload["target"],
            "deadline": payload.get("deadline"),
            "weightage": float(payload["weightage"]),
            "approval_status": "draft",
            "sheet_status": "draft",
            "locked": False,
            "is_shared": True,
            "shared_group_id": shared_group_id,
            "created_by": g.current_user["id"],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        result = g.db.goals.insert_one(doc)
        doc["_id"] = result.inserted_id
        created.append(clean_doc(doc))
    write_audit(g.db, g.current_user["id"], "ASSIGN_SHARED_GOAL", "goal", shared_group_id, {"employee_count": len(created)})
    return respond(*ok({"goals": created}, "Shared goal assigned", 201))


@manager_bp.post("/updates/<update_id>/review")
@auth_required("manager", "admin")
def review_update(update_id):
    update = clean_doc(g.db.quarterly_updates.find_one({"_id": update_id}))
    if not update:
        return respond(*error("Update not found", 404))
    payload = request.get_json() or {}
    comment = {
        "_id": uuid4().hex,
        "goal_id": update["goal_id"],
        "update_id": update_id,
        "author_id": g.current_user["id"],
        "comment": payload.get("comment", ""),
        "created_at": now_iso(),
    }
    result = g.db.comments.insert_one(comment)
    g.db.quarterly_updates.update_one({"_id": update_id}, {"$set": {"manager_reviewed": True, "reviewed_at": now_iso()}})
    write_audit(g.db, g.current_user["id"], "REVIEW_QUARTERLY_UPDATE", "quarterly_update", update_id)
    comment["_id"] = result.inserted_id
    return respond(*ok({"comment": clean_doc(comment)}, "Feedback added"))


@manager_bp.get("/dashboard")
@auth_required("manager", "admin")
def dashboard():
    manager_id = g.current_user["id"]
    goals = rows(g.db.goals, {} if g.current_user["role"] == "admin" else {"manager_id": manager_id})
    updates = rows(g.db.quarterly_updates)
    users = rows(g.db.users)
    logs = rows(g.db.audit_logs)
    employee_ids = {goal["employee_id"] for goal in goals}
    relevant_updates = [item for item in updates if item["employee_id"] in employee_ids]
    relevant_logs = [log for log in logs if log.get("actor_id") in employee_ids or log.get("actor_id") == manager_id]
    summary = dashboard_summary(goals, relevant_updates, users, relevant_logs)
    summary["goals"] = goals
    summary["updates"] = relevant_updates
    return respond(*ok(summary))
