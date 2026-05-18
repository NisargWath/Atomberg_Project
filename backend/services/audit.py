from datetime import datetime, timezone


def write_audit(db, actor_id, action, entity_type, entity_id=None, metadata=None, previous_value=None, updated_value=None):
    metadata = metadata or {}
    if previous_value is not None:
        metadata["previous_value"] = previous_value
    if updated_value is not None:
        metadata["updated_value"] = updated_value
    entry = {
        "actor_id": actor_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return db.audit_logs.insert_one(entry)
