from datetime import datetime, timedelta, timezone

import jwt
from uuid import uuid4

from flask import Blueprint, g, request
from werkzeug.security import check_password_hash, generate_password_hash

from config.settings import settings
from middleware.auth import auth_required
from models.store import clean_doc, now_iso
from services.audit import write_audit
from utils.responses import error, ok, respond

auth_bp = Blueprint("auth", __name__)


def public_user(user):
    user = clean_doc(user)
    if user:
        user.pop("password_hash", None)
    return user


def make_token(user):
    payload = {
        "sub": str(user["_id"]),
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=10),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@auth_bp.post("/login")
def login():
    payload = request.get_json() or {}
    email = (payload.get("email") or "").lower().strip()
    password = payload.get("password") or ""
    user = g.db.users.find_one({"email": email})
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return respond(*error("Invalid email or password", 401))

    write_audit(g.db, str(user["_id"]), "LOGIN", "user", str(user["_id"]))
    return respond(*ok({"token": make_token(user), "user": public_user(user)}, "Logged in"))


@auth_bp.get("/me")
@auth_required()
def me():
    return respond(*ok({"user": public_user(g.current_user)}))


@auth_bp.post("/seed-demo")
def seed_demo_user():
    payload = request.get_json() or {}
    role = payload.get("role", "employee")
    if role not in {"employee", "manager", "admin"}:
        return respond(*error("Invalid role"))
    email = payload.get("email", f"{role}@demo.com").lower()
    existing = g.db.users.find_one({"email": email})
    if existing:
        return respond(*ok({"user": public_user(existing)}, "Demo user already exists"))
    doc = {
        "_id": uuid4().hex,
        "name": payload.get("name", f"Demo {role.title()}"),
        "email": email,
        "role": role,
        "department": payload.get("department", "Product"),
        "manager_id": payload.get("manager_id"),
        "password_hash": generate_password_hash(payload.get("password", "password123")),
        "created_at": now_iso(),
    }
    result = g.db.users.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return respond(*ok({"user": public_user(doc)}, "Demo user created", 201))
