import csv
from io import StringIO

from uuid import uuid4

from flask import Blueprint, Response, g, request
from werkzeug.security import generate_password_hash

from middleware.auth import auth_required
from models.store import clean_doc, now_iso
from services.analytics import dashboard_summary, enrich_audit_logs
from services.audit import write_audit
from utils.responses import error, ok, respond

admin_bp = Blueprint("admin", __name__)


def many(collection, query=None):
    return [clean_doc(row) for row in collection.find(query or {})]


@admin_bp.get("/users")
@auth_required("admin", "manager")
def users():
    users = many(g.db.users)
    for user in users:
        user.pop("password_hash", None)
    return respond(*ok({"users": users}))


@admin_bp.post("/users")
@auth_required("admin")
def create_user():
    payload = request.get_json() or {}
    if payload.get("role") not in {"employee", "manager", "admin"}:
        return respond(*error("Invalid role"))
    if g.db.users.find_one({"email": payload.get("email", "").lower()}):
        return respond(*error("Email already exists", 409))
    doc = {
        "_id": uuid4().hex,
        "name": payload.get("name"),
        "email": payload.get("email", "").lower(),
        "role": payload.get("role"),
        "department": payload.get("department", ""),
        "manager_id": payload.get("manager_id"),
        "password_hash": generate_password_hash(payload.get("password", "password123")),
        "created_at": now_iso(),
    }
    result = g.db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    doc = clean_doc(doc)
    doc.pop("password_hash", None)
    write_audit(g.db, g.current_user["id"], "CREATE_USER", "user", doc["id"])
    return respond(*ok({"user": doc}, "User created", 201))


@admin_bp.post("/goals/<goal_id>/unlock")
@auth_required("admin")
def unlock_goal(goal_id):
    goal = clean_doc(g.db.goals.find_one({"_id": goal_id}))
    if not goal:
        return respond(*error("Goal not found", 404))
    g.db.goals.update_one({"_id": goal_id}, {"$set": {"locked": False, "approval_status": "rework", "sheet_status": "rework", "updated_at": now_iso()}})
    write_audit(g.db, g.current_user["id"], "UNLOCK_GOAL", "goal", goal_id)
    return respond(*ok({"goal": clean_doc(g.db.goals.find_one({"_id": goal_id}))}, "Goal unlocked"))


@admin_bp.get("/audit-logs")
@auth_required("admin")
def audit_logs():
    logs = enrich_audit_logs(many(g.db.audit_logs), many(g.db.users))
    return respond(*ok({"logs": logs}))


@admin_bp.get("/dashboard")
@auth_required("admin")
def dashboard():
    goals = many(g.db.goals)
    updates = many(g.db.quarterly_updates)
    users = many(g.db.users)
    logs = many(g.db.audit_logs)
    summary = dashboard_summary(goals, updates, users, logs)
    summary["goals"] = goals
    summary["updates"] = updates
    return respond(*ok(summary))


@admin_bp.get("/reports/export")
@auth_required("admin")
def export_report():
    report_type = request.args.get("type", "goals")
    goals = many(g.db.goals)
    updates = many(g.db.quarterly_updates)
    users = many(g.db.users)
    logs = many(g.db.audit_logs)
    summary = dashboard_summary(goals, updates, users, logs)

    output = StringIO()
    writer = csv.writer(output)
    if report_type == "quarterly":
        writer.writerow(["Employee", "Goal ID", "Quarter", "Actual Achievement", "Progress %", "Status", "Manager Reviewed", "Updated At"])
        for update in updates:
            writer.writerow([
                next((user.get("name") for user in users if user["id"] == update.get("employee_id")), update.get("employee_id")),
                update.get("goal_id"),
                update.get("quarter"),
                update.get("actual_achievement"),
                update.get("progress"),
                update.get("status"),
                "Yes" if update.get("manager_reviewed") else "No",
                update.get("updated_at"),
            ])
    elif report_type == "team":
        writer.writerow(["Employee", "Department", "Goals", "Approved Goals", "Average Progress %"])
        for employee in summary["chart_data"]["employee_progress"]:
            writer.writerow([employee["name"], employee.get("department"), employee["goals"], employee["approved"], employee["progress"]])
    elif report_type == "organization":
        writer.writerow(["Metric", "Value"])
        for key, value in summary["stats"].items():
            writer.writerow([key.replace("_", " ").title(), value])
    else:
        writer.writerow(["Employee", "Manager", "Department", "Title", "Thrust Area", "Target", "Weightage", "Approval", "Locked", "Latest Quarter", "Progress %"])
        for goal in summary["goal_rows"]:
            writer.writerow([
                goal.get("employee_name"),
                goal.get("manager_name"),
                goal.get("department"),
                goal.get("title"),
                goal.get("thrust_area"),
                goal.get("target"),
                goal.get("weightage"),
                goal.get("approval_status"),
                "Yes" if goal.get("locked") else "No",
                goal.get("latest_quarter", ""),
                goal.get("latest_progress", ""),
            ])
    filename = f"{report_type}-report.csv"
    write_audit(g.db, g.current_user["id"], "EXPORT_REPORT", "report", filename, {"report_type": report_type})
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
