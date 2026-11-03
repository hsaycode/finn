"""Phone notifications through the browser's Web Push, so an alert can reach a closed app.

This stays off until VAPID keys are set (see scripts/vapid_keys.py), and the app doesn't offer it
until then. Each phone stores its own limits and muted categories here, because the settings it
chose on the Watch tab live on the phone and the server sending alerts can't otherwise see them.
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime
from urllib.parse import urlparse

from .store import db

MAX_PER_RUN = 5
MAX_PHONES = 500
# Whoever subscribes supplies the address the server will post to, so only browser vendors' push services are accepted.
_PUSH_HOSTS = ("fcm.googleapis.com", ".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")


def public_key() -> str | None:
    return os.environ.get("VAPID_PUBLIC_KEY")


def enabled() -> bool:
    return bool(public_key() and os.environ.get("VAPID_PRIVATE_KEY") and importlib.util.find_spec("pywebpush"))


def valid_endpoint(url: str) -> bool:
    parts = urlparse(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or (h.startswith(".") and host.endswith(h)) for h in _PUSH_HOSTS)


def subscribe(subscription: dict, move: float, vol: float, muted: list[str]) -> bool:
    endpoint = subscription.get("endpoint") or ""
    keys = subscription.get("keys") or {}
    if not valid_endpoint(endpoint) or not keys.get("p256dh") or not keys.get("auth"):
        return False
    conn = db()
    if conn.execute("SELECT COUNT(*) n FROM push_subs").fetchone()["n"] >= MAX_PHONES:
        return False
    settings = {"move": move, "vol": vol, "muted": [str(m) for m in muted][:30]}
    # A new phone starts from now, so turning this on doesn't send a backlog.
    conn.execute(
        "INSERT INTO push_subs (endpoint, subscription, settings, seen) VALUES (?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription, settings=excluded.settings",
        (endpoint, json.dumps(subscription), json.dumps(settings), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return True


def unsubscribe(endpoint: str) -> None:
    db().execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
    db().commit()


def _deliver(subscription: dict, payload: dict) -> int:
    """Returns the push service's status. 404 and 410 mean this phone has gone."""
    from pywebpush import WebPushException, webpush

    try:
        r = webpush(
            subscription_info=subscription, data=json.dumps(payload), ttl=86400,
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")},
        )
        return r.status_code
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0


def send_new(alerts_for, deliver=None) -> int:
    """Send each phone what's new under its own limits. alerts_for(move, vol, muted) returns newest first."""
    if deliver is None:
        if not enabled():
            return 0
        deliver = _deliver
    conn = db()
    sent = 0
    for row in conn.execute("SELECT * FROM push_subs").fetchall():
        settings = json.loads(row["settings"])
        messages = alerts_for(settings["move"], settings["vol"], settings["muted"])
        fresh = [m for m in messages if m["ts"] > row["seen"]]
        if not fresh:
            continue
        gone = False
        for m in fresh[:MAX_PER_RUN]:
            status = deliver(json.loads(row["subscription"]), {"title": f"{m['symbol']}: {m['title']}", "body": m["text"], "url": f"/research/{m['symbol']}"})
            if status in (404, 410):
                conn.execute("DELETE FROM push_subs WHERE endpoint=?", (row["endpoint"],))
                gone = True
                break
            if 200 <= status < 300:
                sent += 1
        if not gone:
            # Keep what was seen in step with what was offered, so an outage doesn't replay itself as a flood.
            conn.execute("UPDATE push_subs SET seen=? WHERE endpoint=?", (fresh[0]["ts"], row["endpoint"]))
        conn.commit()
    return sent
