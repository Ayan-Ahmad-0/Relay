"""Root launcher for external sink service."""

import os

from relay.sink import run_service

if __name__ == "__main__":
    port = int(os.getenv("SINK_PORT", "8001"))
    run_service(port=port)
