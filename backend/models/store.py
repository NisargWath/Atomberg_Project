import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from werkzeug.security import generate_password_hash

from config.settings import settings


COLLECTIONS = [
    "users",
    "goals",
    "goal_sheets",
    "quarterly_updates",
    "comments",
    "audit_logs",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean_doc(doc):
    if not doc:
        return doc
    item = dict(doc)
    if "_id" in item:
        item["id"] = str(item.pop("_id"))
    return item


def result(**kwargs):
    return type("Result", (), kwargs)


class SQLiteCollection:
    def __init__(self, connection, lock, name):
        self.connection = connection
        self.lock = lock
        self.name = name

    def _match(self, doc, query):
        for key, expected in (query or {}).items():
            if isinstance(expected, dict):
                if "$in" in expected and doc.get(key) not in expected["$in"]:
                    return False
                if "$ne" in expected and doc.get(key) == expected["$ne"]:
                    return False
                continue
            if doc.get(key) != expected:
                return False
        return True

    def _load_all(self):
        with self.lock:
            rows = self.connection.execute(f"SELECT document FROM {self.name}").fetchall()
        return [json.loads(row["document"]) for row in rows]

    def _save(self, doc):
        payload = json.dumps(doc, sort_keys=True)
        with self.lock:
            self.connection.execute(
                f"INSERT OR REPLACE INTO {self.name} (_id, document) VALUES (?, ?)",
                (doc["_id"], payload),
            )
            self.connection.commit()

    def find(self, query=None):
        return [deepcopy(row) for row in self._load_all() if self._match(row, query)]

    def find_one(self, query=None):
        for row in self._load_all():
            if self._match(row, query):
                return deepcopy(row)
        return None

    def insert_one(self, doc):
        item = deepcopy(doc)
        item["_id"] = str(item.get("_id") or uuid4().hex)
        self._save(item)
        return result(inserted_id=item["_id"])

    def update_one(self, query, update):
        for row in self._load_all():
            if self._match(row, query):
                for key, value in update.get("$set", {}).items():
                    row[key] = value
                for key, value in update.get("$inc", {}).items():
                    row[key] = row.get(key, 0) + value
                self._save(row)
                return result(matched_count=1, modified_count=1)
        return result(matched_count=0, modified_count=0)

    def delete_one(self, query):
        for row in self._load_all():
            if self._match(row, query):
                with self.lock:
                    self.connection.execute(f"DELETE FROM {self.name} WHERE _id = ?", (row["_id"],))
                    self.connection.commit()
                return result(deleted_count=1)
        return result(deleted_count=0)


class SQLiteDB:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        if not self.db_path.is_absolute():
            self.db_path = Path(__file__).resolve().parents[1] / self.db_path
        self.lock = RLock()
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_tables()
        for collection in COLLECTIONS:
            setattr(self, collection, SQLiteCollection(self.connection, self.lock, collection))
        self.seed()

    def _create_tables(self):
        with self.lock:
            for collection in COLLECTIONS:
                self.connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {collection} (
                        _id TEXT PRIMARY KEY,
                        document TEXT NOT NULL
                    )
                    """
                )
            self.connection.commit()

    def seed(self):
        if self.users.find_one({}):
            return
        users = [
            {
                "_id": "employee-demo",
                "name": "Asha Employee",
                "email": "employee@demo.com",
                "role": "employee",
                "manager_id": "manager-demo",
            },
            {
                "_id": "manager-demo",
                "name": "Rahul Manager",
                "email": "manager@demo.com",
                "role": "manager",
            },
            {
                "_id": "admin-demo",
                "name": "Priya HR Admin",
                "email": "admin@demo.com",
                "role": "admin",
            },
        ]
        for user in users:
            user["password_hash"] = generate_password_hash("password123")
            user["department"] = user.get("department", "Product")
            user["created_at"] = now_iso()
            self.users.insert_one(user)


def get_db():
    print(f"Using SQLite database at {settings.SQLITE_DB_PATH}")
    return SQLiteDB(settings.SQLITE_DB_PATH)
