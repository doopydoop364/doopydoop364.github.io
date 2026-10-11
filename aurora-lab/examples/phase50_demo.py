"""Free, mocked Phase 5 employee inference demonstration. No server or API key required."""
from __future__ import annotations

import json

from aurora.employee_runtime import EmployeeRuntime
from aurora.employees import EmployeeTurn, directory
from aurora.providers import ProviderError


class MockLocal:
    def __init__(self):
        self.fail_next = False
        self.calls: list[dict[str, object]] = []

    def infer(self, system, messages, *, model, output_limit, timeout):
        self.calls.append({"identity": system, "messages": messages, "model": model})
        if self.fail_next:
            self.fail_next = False
            raise ProviderError("mock transport interruption", category="connection",
                                retriable=True, uncertain=True)
        name = "Priya" if "Priya Solano" in system else "Felix"
        return EmployeeTurn(f"{name}: I will review this within my department.", 14, 11, "local", model)


def main() -> None:
    people = directory()
    priya = "priya.solano@meridian.internal"
    felix = "felix.morgan@meridian.internal"
    malik = "malik.chen@meridian.internal"
    settings = {"local_endpoint": "http://127.0.0.1:8888/v1", "local_model": "mock-small",
                "overrides": {priya: {"backend": "local"}, felix: {"backend": "local"}}}
    mock = MockLocal()
    factory = lambda backend, model: mock
    runtime = EmployeeRuntime(settings, people, backend_factory=factory)
    for email, content in ((priya, "Private Atlas handoff status"),
                           (felix, "Access-control review status"),
                           (malik, "Routing drill status")):
        request_id = runtime.queue(people[email], {"sender": "aurora@meridian.internal",
            "recipient": email, "subject": "Operations", "content": content}, "2026-10-10T10:00:00Z")
        outcome = runtime.process(request_id)
        print(json.dumps({"recipient": email, "response": outcome["reply"]["content"],
            "backend": outcome["provider"], "usage": outcome["usage"]}))
    mock.fail_next = True
    request_id = runtime.queue(people[felix], {"sender": "aurora@meridian.internal",
        "recipient": felix, "subject": "Follow-up", "content": "Can you verify the reader group?"},
        "2026-10-10T11:00:00Z")
    assert runtime.process(request_id) is None
    saved = json.loads(json.dumps(runtime.snapshot()))
    restored = EmployeeRuntime.restore(saved, people, 0, backend_factory=factory)
    assert restored.pending[request_id]["status"] == "uncertain"
    restored.requeue_uncertain(request_id)  # Explicit operator decision in this free mock demo.
    recovered = restored.process(request_id)
    assert recovered and restored.process(request_id) is None
    assert "Private Atlas" not in str(mock.calls[1]["messages"])
    print(json.dumps({"recovered_request": request_id, "attempts": restored.pending[request_id]["attempt"],
        "usage": restored.summary(), "isolated_histories": True}, indent=2))


if __name__ == "__main__":
    main()
