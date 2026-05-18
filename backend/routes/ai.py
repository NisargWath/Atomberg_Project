from flask import Blueprint, g

from middleware.auth import auth_required
from models.store import clean_doc
from services.ai_insights import generate_insights
from services.analytics import dashboard_summary
from utils.responses import ok, respond

ai_bp = Blueprint("ai", __name__)


def many(collection, query=None):
    return [clean_doc(row) for row in collection.find(query or {})]


@ai_bp.get("/employee-insights")
@auth_required("employee")
def employee_insights():
    employee_id = g.current_user["id"]
    goals = many(g.db.goals, {"employee_id": employee_id})
    updates = many(g.db.quarterly_updates, {"employee_id": employee_id})
    users = many(g.db.users)
    logs = many(g.db.audit_logs, {"actor_id": employee_id})
    summary = dashboard_summary(goals, updates, users, logs)
    return respond(*ok(generate_insights(summary, "employee")))


@ai_bp.get("/manager-insights")
@auth_required("manager", "admin")
def manager_insights():
    manager_id = g.current_user["id"]
    goals = many(g.db.goals, {} if g.current_user["role"] == "admin" else {"manager_id": manager_id})
    employee_ids = {goal.get("employee_id") for goal in goals}
    updates = [update for update in many(g.db.quarterly_updates) if update.get("employee_id") in employee_ids]
    users = many(g.db.users)
    logs = [log for log in many(g.db.audit_logs) if log.get("actor_id") in employee_ids or log.get("actor_id") == manager_id]
    summary = dashboard_summary(goals, updates, users, logs)
    return respond(*ok(generate_insights(summary, "manager")))


@ai_bp.get("/admin-insights")
@auth_required("admin")
def admin_insights():
    summary = dashboard_summary(many(g.db.goals), many(g.db.quarterly_updates), many(g.db.users), many(g.db.audit_logs))
    return respond(*ok(generate_insights(summary, "admin")))
