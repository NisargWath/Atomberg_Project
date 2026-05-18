import argparse
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from werkzeug.security import generate_password_hash

from config.settings import settings
from models.store import COLLECTIONS, SQLiteDB, now_iso
from services.progress import calculate_progress


def iso(days_offset=0):
    return (datetime.now(timezone.utc) + timedelta(days=days_offset)).isoformat()


def reset_database(db):
    with db.lock:
        for collection in COLLECTIONS:
            db.connection.execute(f"DELETE FROM {collection}")
        db.connection.commit()


def user(_id, name, email, role, department, manager_id=None):
    return {
        "_id": _id,
        "name": name,
        "email": email,
        "role": role,
        "department": department,
        "manager_id": manager_id,
        "password_hash": generate_password_hash("password123"),
        "created_at": iso(-35),
    }


def goal(employee_id, manager_id, title, thrust_area, target, weightage, status="approved", metric_type="min", shared=False, group_id=None):
    locked = status == "approved"
    return {
        "_id": uuid4().hex,
        "employee_id": employee_id,
        "manager_id": manager_id,
        "title": title,
        "description": f"Deliver measurable progress for {title.lower()} aligned to quarterly business priorities.",
        "thrust_area": thrust_area,
        "uom": "percentage" if "Rate" in title or "Score" in title else "numeric",
        "metric_type": metric_type,
        "target": target,
        "deadline": iso(75),
        "weightage": weightage,
        "approval_status": status,
        "sheet_status": status,
        "locked": locked,
        "is_shared": shared,
        "shared_group_id": group_id,
        "created_by": manager_id if shared else employee_id,
        "created_at": iso(-25),
        "updated_at": iso(-8 if status == "approved" else -2),
        "manager_comment": "Approved for FY26 execution." if status == "approved" else "",
    }


def audit(actor_id, action, entity_type, entity_id, days_offset, metadata=None):
    return {
        "_id": uuid4().hex,
        "actor_id": actor_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": metadata or {},
        "created_at": iso(days_offset),
    }


def insert_quarterly_updates(db, goals):
    actuals = {
        "Q1": [72, 64, 81, 58, 45, 70, 77, 66, 88, 42],
        "Q2": [83, 72, 88, 68, 54, 79, 86, 73, 92, 51],
    }
    updates = []
    for index, item in enumerate([goal for goal in goals if goal["approval_status"] == "approved"]):
        for quarter in ["Q1", "Q2"]:
            actual = actuals[quarter][index % len(actuals[quarter])]
            progress = calculate_progress(item, actual)
            status = "completed" if progress >= 95 else "on_track" if progress >= 60 else "not_started"
            update = {
                "_id": uuid4().hex,
                "goal_id": item["_id"],
                "employee_id": item["employee_id"],
                "quarter": quarter,
                "actual_achievement": actual,
                "progress": progress,
                "status": status,
                "notes": f"{quarter} progress captured from demo review cycle.",
                "created_at": iso(-20 if quarter == "Q1" else -5),
                "updated_at": iso(-19 if quarter == "Q1" else -4),
                "manager_reviewed": quarter == "Q1" or progress >= 70,
                "reviewed_at": iso(-18 if quarter == "Q1" else -3) if quarter == "Q1" or progress >= 70 else None,
            }
            updates.append(update)
            db.quarterly_updates.insert_one(update)
    return updates


def main():
    parser = argparse.ArgumentParser(description="Seed realistic AtomQuest demo data.")
    parser.add_argument("--if-empty", action="store_true", help="Seed only when the goals table is empty.")
    args = parser.parse_args()

    db = SQLiteDB(settings.SQLITE_DB_PATH)
    if args.if_empty and db.goals.find_one({}):
        print(f"Demo data already exists in {settings.SQLITE_DB_PATH}; skipping seed.")
        return

    reset_database(db)

    users = [
        user("admin-demo", "Priya Shah", "admin@demo.com", "admin", "People Operations"),
        user("manager-growth", "Rahul Mehta", "manager@demo.com", "manager", "Growth"),
        user("manager-product", "Neha Iyer", "manager2@demo.com", "manager", "Product"),
        user("employee-demo", "Asha Rao", "employee@demo.com", "employee", "Growth", "manager-growth"),
        user("employee-2", "Kabir Singh", "kabir@demo.com", "employee", "Growth", "manager-growth"),
        user("employee-3", "Meera Nair", "meera@demo.com", "employee", "Product", "manager-product"),
        user("employee-4", "Arjun Patel", "arjun@demo.com", "employee", "Product", "manager-product"),
    ]
    for item in users:
        db.users.insert_one(item)

    shared_group = f"shared-{uuid4().hex}"
    goals = [
        goal("employee-demo", "manager-growth", "Improve onboarding completion rate", "Customer Experience", 90, 35),
        goal("employee-demo", "manager-growth", "Reduce support escalation count", "Operational Excellence", 20, 30, metric_type="max"),
        goal("employee-demo", "manager-growth", "Department NPS Score", "Shared KPI", 85, 35, shared=True, group_id=shared_group),
        goal("employee-2", "manager-growth", "Increase qualified pipeline", "Revenue Growth", 120, 40),
        goal("employee-2", "manager-growth", "Improve campaign ROI", "Revenue Growth", 75, 25),
        goal("employee-2", "manager-growth", "Department NPS Score", "Shared KPI", 85, 35, shared=True, group_id=shared_group),
        goal("employee-3", "manager-product", "Ship roadmap milestones", "Product Delivery", 8, 40),
        goal("employee-3", "manager-product", "Improve feature adoption", "Product Adoption", 65, 30),
        goal("employee-3", "manager-product", "Department NPS Score", "Shared KPI", 85, 30, shared=True, group_id=shared_group),
        goal("employee-4", "manager-product", "Reduce defect leakage", "Quality", 12, 40, metric_type="max"),
        goal("employee-4", "manager-product", "Improve release predictability", "Delivery Excellence", 90, 30),
        goal("employee-4", "manager-product", "Department NPS Score", "Shared KPI", 85, 30, shared=True, group_id=shared_group),
        goal("employee-2", "manager-growth", "Draft partner enablement plan", "Strategic Initiatives", 100, 10, status="submitted"),
        goal("employee-4", "manager-product", "Draft Q3 platform scorecard", "Strategic Initiatives", 100, 10, status="submitted"),
    ]
    for item in goals:
        db.goals.insert_one(item)

    for employee_id, manager_id in [
        ("employee-demo", "manager-growth"),
        ("employee-2", "manager-growth"),
        ("employee-3", "manager-product"),
        ("employee-4", "manager-product"),
    ]:
        employee_goal_ids = [item["_id"] for item in goals if item["employee_id"] == employee_id]
        db.goal_sheets.insert_one({
            "_id": uuid4().hex,
            "employee_id": employee_id,
            "manager_id": manager_id,
            "goal_ids": employee_goal_ids,
            "status": "approved" if len(employee_goal_ids) > 2 else "submitted",
            "submitted_at": iso(-14),
        })

    updates = insert_quarterly_updates(db, goals)

    for update in updates[:8]:
        db.comments.insert_one({
            "_id": uuid4().hex,
            "goal_id": update["goal_id"],
            "update_id": update["_id"],
            "author_id": next(goal["manager_id"] for goal in goals if goal["_id"] == update["goal_id"]),
            "comment": "Good progress. Keep focus on measurable outcomes for the next review.",
            "created_at": iso(-3),
        })

    events = [
        audit("admin-demo", "SEED_DEMO_DATA", "database", "goal_portal", -30, {"updated_value": "Demo data loaded"}),
        audit("employee-demo", "SUBMIT_GOAL_SHEET", "goal_sheet", "employee-demo", -14, {"goal_count": 3}),
        audit("manager-growth", "APPROVE_GOAL_SHEET", "user", "employee-demo", -12, {"previous_value": "submitted", "updated_value": "approved"}),
        audit("manager-product", "ASSIGN_SHARED_GOAL", "goal", shared_group, -11, {"employee_count": 4}),
        audit("employee-3", "ADD_QUARTERLY_UPDATE", "quarterly_update", updates[5]["_id"], -5, {"updated_value": {"progress": updates[5]["progress"]}}),
        audit("manager-product", "REVIEW_QUARTERLY_UPDATE", "quarterly_update", updates[5]["_id"], -3, {"updated_value": "Reviewed"}),
        audit("admin-demo", "EXPORT_REPORT", "report", "organization-report.csv", -1, {"report_type": "organization"}),
    ]
    for event in events:
        db.audit_logs.insert_one(event)

    print(f"Seeded {len(users)} users, {len(goals)} goals, {len(updates)} quarterly updates into {settings.SQLITE_DB_PATH}")
    print("Demo password for all users: password123")


if __name__ == "__main__":
    main()
