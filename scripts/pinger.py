"""External 5-minute keepalive pinger to prevent Render free-tier spinning down."""

import os
import time

import httpx

RELAY_URL = os.getenv("RENDER_SERVICE_URL", "http://localhost:8000/docs")
INTERVAL_SECONDS = int(os.getenv("PING_INTERVAL_SECONDS", "300"))  # 5 minutes


def ping_service() -> None:
    print(f"[*] Starting keepalive pinger for {RELAY_URL} every {INTERVAL_SECONDS}s...")
    with httpx.Client(timeout=10.0) as client:
        while True:
            try:
                res = client.get(RELAY_URL)
                print(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ping {RELAY_URL} -> HTTP {res.status_code}"
                )
            except Exception as err:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ping failed: {err}")
            time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    ping_service()
