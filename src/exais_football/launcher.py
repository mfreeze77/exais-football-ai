"""Open only the newly launched instance after its local health check succeeds."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def open_when_ready(origin: str, instance_id: str) -> threading.Thread:
    def launch():
        deadline = time.monotonic() + 30
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            try:
                with opener.open(origin + "/api/health", timeout=1) as response:
                    value = json.loads(response.read(65536))
                if value.get("instance_id") == instance_id:
                    webbrowser.open(origin)
                    return
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.2)

    thread = threading.Thread(target=launch, name="exais-open-local-browser", daemon=True)
    thread.start()
    return thread
