from flask import jsonify


def ok(data=None, message="OK", status=200):
    payload = {"success": True, "message": message}
    if data is not None:
        payload["data"] = data
    return payload, status


def error(message, status=400, details=None):
    payload = {"success": False, "message": message}
    if details:
        payload["details"] = details
    return payload, status


def respond(payload, status=200):
    return jsonify(payload), status
