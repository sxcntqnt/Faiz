from datetime import datetime

def _fmt_time(ts):
    if not ts:
        return "—"

    delta = datetime.utcnow().timestamp() - ts

    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _status_icon(status):
    try:
        s = status.value if hasattr(status, "value") else str(status)
    except Exception:
        s = str(status)

    if "run" in s:
        return "🟢"
    if "stop" in s:
        return "🔴"
    if "degrad" in s:
        return "🟠"
    return "⚪"


