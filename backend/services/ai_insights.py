import json
import urllib.error
import urllib.request

from config.settings import settings

FALLBACK_MESSAGE = "AI insights temporarily unavailable."


def compact_dashboard_payload(summary, role):
    return {
        "role": role,
        "stats": summary.get("stats", {}),
        "goals_by_status": summary.get("chart_data", {}).get("goals_by_status", []),
        "quarterly_progress": summary.get("chart_data", {}).get("quarterly_progress", []),
        "employee_progress": summary.get("chart_data", {}).get("employee_progress", [])[:8],
        "recent_updates": summary.get("recent_updates", [])[:5],
    }


def build_prompt(summary, role):
    payload = compact_dashboard_payload(summary, role)
    return (
        "Analyze the following quarterly goal tracking data and generate a concise professional "
        "enterprise performance summary including strengths, risks, and recommendations. "
        "Return only 3 to 5 short bullet points. Avoid markdown headings. Keep it business-oriented.\n\n"
        f"Dashboard data:\n{json.dumps(payload, indent=2)}"
    )


def fallback_insights(summary, role):
    stats = summary.get("stats", {})
    progress = stats.get("average_progress", 0)
    pending = stats.get("pending_approvals", 0)
    approved = stats.get("approved_goals", 0)
    total = stats.get("total_goals", 0)
    label = "team" if role == "manager" else "organization" if role == "admin" else "goal plan"
    return [
        f"{label.title()} progress is currently tracking at {progress}% based on latest quarterly updates.",
        f"{approved} of {total} goals are approved and ready for structured performance tracking.",
        f"{pending} pending approval items should be reviewed to keep the workflow moving.",
        "Continue focusing manager check-ins on goals with lower progress or missing quarterly updates.",
    ]


def parse_bullets(text):
    lines = []
    for line in (text or "").splitlines():
        cleaned = line.strip().lstrip("-*•0123456789. ").strip()
        if cleaned:
            lines.append(cleaned)
    if not lines and text:
        lines = [text.strip()]
    return lines[:5]


def request_gemini(prompt):
    if not settings.GEMINI_API_KEY:
        return None
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 220,
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=settings.GEMINI_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts)


def generate_insights(summary, role):
    prompt = build_prompt(summary, role)
    try:
        text = request_gemini(prompt)
        if not text:
            return {"available": False, "message": FALLBACK_MESSAGE, "insights": fallback_insights(summary, role)}
        bullets = parse_bullets(text)
        return {"available": True, "message": "AI insights generated.", "insights": bullets}
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, OSError):
        return {"available": False, "message": FALLBACK_MESSAGE, "insights": fallback_insights(summary, role)}
