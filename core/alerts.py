"""
DocVault Alert Bus.
Central dispatcher for system-level notifications and security alerts.
Supports UI Toasts, Console Logs, and optional Webhooks/NTFY.
"""
import time
import json
import httpx
from datetime import datetime, timezone
from core.settings import settings
from core import logger

# In-memory queue for current session alerts (cleared on restart)
# Persisted alerts should go to logs.db (handled by ExtractorLogger if related to tasks)
_SESSION_ALERTS = []
MAX_SESSION_ALERTS = 50

def send_alert(title: str, message: str, level: str = 'info', source: str = 'system'):
    """
    Broadcast a system alert to all active channels.
    Levels: info, success, warning, error, critical
    """
    alert = {
        "id": int(time.time() * 1000),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "message": message,
        "level": level.lower(),
        "source": source
    }

    # 1. Console Log
    if level == 'critical': logger.critical(f"ALERT: {title} - {message}", ext=source)
    elif level == 'error':   logger.error(f"{title}: {message}", ext=source)
    elif level == 'warning': logger.warn(f"{title}: {message}", ext=source)
    else:                    logger.info(f"{title}: {message}", ext=source)

    # 2. Session Queue (for UI Polling)
    _SESSION_ALERTS.append(alert)
    if len(_SESSION_ALERTS) > MAX_SESSION_ALERTS:
        _SESSION_ALERTS.pop(0)

    # 3. External Webhooks / NTFY
    _dispatch_remote(alert)

def get_session_alerts(since_id: int = 0) -> list[dict]:
    """Returns alerts created after since_id."""
    return [a for a in _SESSION_ALERTS if a['id'] > since_id]

def _dispatch_remote(alert: dict):
    """Optionally send to ntfy.sh or custom webhook."""
    # NTFY Integration
    ntfy_url = settings.get('alerts:ntfy_url')
    if ntfy_url:
        try:
            # Fire and forget (mostly)
            headers = {"Title": alert['title'], "Priority": _get_ntfy_priority(alert['level'])}
            httpx.post(ntfy_url, content=alert['message'], headers=headers, timeout=2.0)
        except Exception as e:
            logger.debug(f"NTFY failed: {e}", ext="alerts")

def _get_ntfy_priority(level: str) -> str:
    return {
        'critical': '5',
        'error': '4',
        'warning': '3',
        'success': '2',
        'info': '1'
    }.get(level, '3')
