"""Optional operator-run smoke test against an already running loopback model server."""
from __future__ import annotations

import argparse

from aurora.employee_runtime import EmployeeRuntime
from aurora.employees import directory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8888/v1")
    parser.add_argument("--model", default="unsloth/Qwen3.5-9B-GGUF")
    args = parser.parse_args()
    priya = "priya.solano@meridian.internal"
    felix = "felix.morgan@meridian.internal"
    people = directory()
    runtime = EmployeeRuntime({"local_endpoint": args.endpoint, "local_model": args.model,
        "timeout": 120, "concurrency": 1,
        "overrides": {priya: {"backend": "local"}, felix: {"backend": "local"}}}, people)
    when = "2026-10-09T09:00:00Z"
    for email, content in ((priya, "I will send Atlas routing validation evidence."),
                           (felix, "Please review the Atlas access-governance owner.")):
        request = runtime.queue(people[email], {"sender": "aurora@meridian.internal",
            "recipient": email, "subject": "Atlas operations review", "content": content,
            "timestamp": when}, when)
        outcome = runtime.process(request)
        if outcome is None:
            raise RuntimeError(f"local employee inference failed: {runtime.pending[request].get('error')}")
        print(f"{people[email].name}: {outcome['reply']['content']}")
    print("Two independent local employee conversations completed.")


if __name__ == "__main__":
    main()
