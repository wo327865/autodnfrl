"""Best-effort ntfy delivery for automation shell wrappers (standard library only)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


CONFIG = Path(__file__).resolve().parents[1] / ".ntfy.json"


def send_notification(title: str, message: str, failed: bool = False) -> bool:
    try:
        config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        if not config.get("enabled", True):
            return True
        server = os.environ.get("NTFY_SERVER", config.get("server", "https://ntfy.sh")).rstrip("/")
        topic = os.environ.get("NTFY_TOPIC", config.get("topic", ""))
        token = os.environ.get("NTFY_TOKEN", config.get("token", ""))
        if not topic:
            print("ntfy is not configured; set NTFY_TOPIC or .ntfy.json", file=sys.stderr)
            return False
        if urlsplit(server).scheme != "https":
            raise ValueError("NTFY_SERVER must use https")
        headers = {
            "Title": title,
            "Tags": "warning" if failed else "white_check_mark",
            "Priority": "high" if failed else "default",
            "Content-Type": "text/plain; charset=utf-8",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            f"{server}/{quote(topic, safe='')}",
            data=message.encode("utf-8"), headers=headers, method="POST",
        )
        with urlopen(request, timeout=10) as response:
            response.read()
        print("ntfy notification accepted by server")
        return True
    except HTTPError as error:
        print(f"ntfy delivery failed (HTTP {error.code}); check topic permissions and NTFY_TOKEN", file=sys.stderr)
    except (OSError, URLError, ValueError, TypeError) as error:
        # Do not print exception details that could contain an authenticated URL.
        print(f"ntfy delivery failed ({type(error).__name__}); workflow exit status preserved", file=sys.stderr)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--phase", default="complete")
    parser.add_argument("--elapsed", type=int, default=0)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    if args.test:
        delivered = send_notification("Notification test", "Notifications are working.")
    else:
        stopped = args.exit_code in (130, 143)
        failed = args.exit_code != 0 and not stopped
        title = "Task stopped" if stopped else "Task failed" if failed else "Task completed"
        phase = {"battle": "stage 1", "maintenance": "stage 2"}.get(args.phase, "the task")
        message = (
            f"Stopped during {phase}." if stopped else
            f"Failed during {phase} (exit {args.exit_code})." if failed else
            "All stages finished successfully."
        )
        message += f" Duration: {args.elapsed // 60}m {args.elapsed % 60}s."
        if failed:
            message += " Check the local log for details."
        delivered = send_notification(title, message, failed=failed)
    raise SystemExit(0 if delivered else 1)


if __name__ == "__main__":
    main()
