from functools import wraps

import jwt
from flask import g, request

from config.settings import settings
from models.store import clean_doc
from utils.responses import error, respond


def auth_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            token = header.replace("Bearer ", "", 1)
            if not token:
                return respond(*error("Missing authorization token", 401))
            try:
                payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
            except jwt.PyJWTError:
                return respond(*error("Invalid or expired token", 401))

            user = clean_doc(g.db.users.find_one({"_id": payload["sub"]}))
            if not user:
                return respond(*error("User not found", 401))
            if roles and user["role"] not in roles:
                return respond(*error("Access denied", 403))
            g.current_user = user
            return fn(*args, **kwargs)

        return wrapper

    return decorator
