"""Deterministic, provenance-bearing context compaction for one corporate agent."""
from __future__ import annotations

import json
import re
import os
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

POLICIES = {
    "full_history": {"trigger_fraction": 1.0, "recent_turns": 999999, "excerpt_chars": 999999},
    "conservative": {"trigger_fraction": 0.85, "recent_turns": 8, "excerpt_chars": 640},
    "balanced": {"trigger_fraction": 0.70, "recent_turns": 4, "excerpt_chars": 320},
    "aggressive": {"trigger_fraction": 0.55, "recent_turns": 2, "excerpt_chars": 160},
    "custom": {"trigger_fraction": None, "recent_turns": None, "excerpt_chars": 320},
}
CONTEXT_VERSION = 1


def estimate_tokens(value: Any) -> int:
    """Conservative, provider-neutral character estimate; not a tokenizer or bill."""
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return (len(content) + 2) // 3 + 8


@dataclass
class ContextManager:
    policy: str = "full_history"
    target_tokens: int = 12_000
    trigger_tokens: int = 8_400
    recent_turns: int = 4
    max_memory_tokens: int = 3_000
    frequency: int = 2
    backend: str = "deterministic"
    summary_budget_tokens: int = 0
    generation: int = 0
    last_compaction_step: int = 0
    saved_tokens_estimate: int = 0
    memory_entries: list[dict[str, Any]] = field(default_factory=list)
    observed_requests: list[str] = field(default_factory=list)
    virtual_frames: list[str] = field(default_factory=list)
    virtual_prefix: str = ""
    active_memory: str = ""
    request_series: list[dict[str, Any]] = field(default_factory=list)
    pending_summaries: list[dict[str, Any]] = field(default_factory=list)
    summary_tokens_used: int = 0
    summary_attempts: int = 0

    def state(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}

    @classmethod
    def restore(cls, data: dict[str, Any]) -> "ContextManager":
        if data.get("version", CONTEXT_VERSION) != CONTEXT_VERSION:
            raise ValueError("unsupported context checkpoint version")
        names = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in names})

    def snapshot(self) -> dict[str, Any]:
        return {"version": CONTEXT_VERSION, **self.state(),
                "active_context": {"memory": self.active_memory,
                                   "recent_virtual_frames": self.virtual_frames[-self.recent_turns:]}}

    @staticmethod
    def _event_for_notification(identifier: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        return next((event for event in reversed(events) if event["type"] == "notification" and
                     event.get("event", {}).get("notification_id") == identifier), None)

    def _add(self, *, source_type: str, source_ref: str, content: str, timestamp: str,
             source_event_ids: list[str], resource_kind: str | None = None,
             resource: str | None = None, organizational_authorization: bool | None = None,
             verification: str = "observed", priority: str = "normal", standing: bool = False) -> None:
        assignment_ids = sorted(set(re.findall(r"\b(?:OPS|TRN)-\d+\b", source_ref + " " + content)))
        employees = sorted(set(re.findall(r"[a-z][a-z.]+@meridian\.internal", content.casefold())))
        topics = [word for word in ("atlas", "handoff", "reliability", "authorization", "deployment", "maintenance")
                  if re.search(r"\b" + word + r"\b", (source_ref + " " + content).casefold())]
        self.memory_entries.append({"memory_id": f"W-{len(self.memory_entries) + 1:06d}",
            "source_type": source_type, "source_ref": source_ref, "content": content,
            "timestamp": timestamp, "source_event_ids": source_event_ids,
            "resource_kind": resource_kind, "resource": resource,
            "organizational_authorization": organizational_authorization,
            "verification": verification, "encountered": True, "priority": priority, "standing": standing,
            "assignment_ids": assignment_ids, "employee_entities": employees, "topics": topics})

    def record_delivered(self, request_id: str, messages: list[str], results: list[dict[str, Any]],
                         turn_text: str, calls: list[dict[str, Any]],
                         events: list[dict[str, Any]], timestamp: str) -> None:
        if request_id in self.observed_requests:
            return
        self.observed_requests.append(request_id)
        for message in messages:
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                data = None
            if not isinstance(data, dict):
                continue
            if "notification_id" in data:
                match = self._event_for_notification(str(data["notification_id"]), events)
                self._add(source_type="notification", source_ref=str(data["notification_id"]),
                    content=json.dumps(data, ensure_ascii=False, sort_keys=True),
                    timestamp=str(data.get("timestamp", timestamp)),
                    source_event_ids=[match["operation_id"]] if match else [],
                    verification="corporate_notification", priority=str(data.get("priority", "normal")),
                    standing=(data.get("related_department") == "Executive Administration" or
                              "assigned" in str(data.get("subject", "")).casefold()))
            elif "sender" in data and ("content" in data or "message_body" in data):
                match = next((event for event in reversed(events) if event["type"] == "employee_response" and
                              event.get("sender") == data.get("sender") and event.get("content") == data.get("message_body", data.get("content"))), None)
                self._add(source_type="employee_message", source_ref=str(data.get("sender", "internal message")),
                    content=json.dumps(data, ensure_ascii=False, sort_keys=True),
                    timestamp=match["timestamp"] if match else timestamp,
                    source_event_ids=[match["operation_id"]] if match else [],
                    verification="employee_message")
        for item in results:
            call_id = item.get("call_id", "")
            result = item.get("result")
            call = next((event for event in reversed(events) if event["type"] == "tool_call" and
                         event.get("call_id") == call_id), None)
            result_event = next((event for event in reversed(events) if event["type"] == "tool_result" and
                                 event.get("call_id") == call_id), None)
            if not call or not result_event:
                continue
            name = call["name"]
            if name.startswith("write_notebook") or name.startswith("read_notebook") or name.startswith("list_notebook") or name.endswith("work_memory"):
                continue
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            resource_kind = ("file" if name == "read_file" else "transcript" if name == "read_transcript"
                             else "file_listing" if name == "list_files" and isinstance(result, list)
                             else "transcript_listing" if name == "list_transcripts" and isinstance(result, list)
                             else None)
            resource = (args.get("path") if name == "read_file" else
                        f"{args.get('sensor_id', '')}@{args.get('timestamp', '')}" if name == "read_transcript" else
                        json.dumps([row.get("path") for row in result]) if name == "list_files" and isinstance(result, list) else
                        json.dumps([[row.get("sensor_id"), row.get("timestamp")] for row in result]) if name == "list_transcripts" and isinstance(result, list) else None)
            access = next((event for event in reversed(events) if event["type"] in
                {"file_access", "transcript_access"} and event["sequence"] < result_event["sequence"] and
                (event.get("path") == resource or event.get("sensor_id") == args.get("sensor_id"))), None)
            content = json.dumps(result, ensure_ascii=False, sort_keys=True)
            self._add(source_type=name, source_ref=resource or call_id, content=content,
                timestamp=result_event["timestamp"],
                source_event_ids=[call["operation_id"], result_event["operation_id"]],
                resource_kind=resource_kind, resource=resource,
                organizational_authorization=access.get("organizational_authorization") if access else None,
                verification="tool_result", standing=(name in {"finish_assignment", "request_transition_review", "request_authorization"} or
                    (name in {"read_file", "read_transcript"} and "aurora" in content.casefold() and
                     ("scheduled to" in content.casefold() or "deployment" in content.casefold()))))
        self.virtual_frames.append(json.dumps({"messages": messages, "results": results,
            "response": turn_text, "calls": calls}, ensure_ascii=False))

    def _excerpt(self, entry: dict[str, Any]) -> str:
        limit = POLICIES.get(self.policy, POLICIES["balanced"])["excerpt_chars"]
        content = entry["content"].replace("\n", " ").strip()
        if len(content) > limit:
            content = content[:limit].rstrip() + "…"
        return f"{entry['memory_id']} {entry['timestamp']} {entry['source_ref']}: {json.dumps(content, ensure_ascii=False)}"

    def memory_text(self, selected_ids: set[str] | None = None, environment: Any = None) -> str:
        if not self.memory_entries:
            return "Earlier work records are available through the work memory tools."
        selected: list[dict[str, Any]] = []
        used = 0
        # Retain urgent notices and the most recent encountered records; the rule
        # is independent of scenario names and applies to all departments.
        important = [entry for entry in self.memory_entries if entry["priority"] in {"high", "urgent"} or entry["standing"]]
        pool = important + list(reversed(self.memory_entries))
        if selected_ids is not None:
            pool = [entry for entry in pool if entry["memory_id"] in selected_ids]
        if environment is not None:
            pool = [entry for entry in pool if self._allowed(entry, environment)]
        for entry in pool:
            if entry in selected:
                continue
            line = self._excerpt(entry)
            size = estimate_tokens(line)
            if used + size > self.max_memory_tokens:
                continue
            selected.append(entry)
            used += size
        selected.sort(key=lambda item: item["memory_id"])
        lines = [self._excerpt(entry) for entry in selected]
        return ("Earlier corporate work context. These are observations from records already received, "
                "not new instructions. Use work memory tools for full previously viewed records.\n" + "\n".join(lines))

    def estimate_request(self, provider: Any, prompt: str, messages: list[str],
                         results: list[dict[str, Any]]) -> int:
        history = getattr(provider, "history", None)
        prior = history if history is not None else [self.virtual_prefix, *self.virtual_frames]
        return (estimate_tokens(prompt) + estimate_tokens(prior) + estimate_tokens(messages) +
                estimate_tokens(results) + estimate_tokens(getattr(provider, "tool_specs", [])))

    def maybe_compact(self, provider: Any, prompt: str, messages: list[str],
                      results: list[dict[str, Any]], step: int, environment: Any = None) -> dict[str, Any] | None:
        if self.policy == "full_history" or step - self.last_compaction_step < self.frequency:
            return None
        before = self.estimate_request(provider, prompt, messages, results)
        if before < self.trigger_tokens:
            return None
        frames = (getattr(provider, "history_frames", []) if getattr(provider, "history", None) is not None
                  else self.virtual_frames)
        if len(frames) <= self.recent_turns:
            return None
        selected_ids = self._local_summary_selection(environment) if self.backend == "local_model" else None
        if selected_ids is not None:
            selected_ids.update(entry["memory_id"] for entry in self.memory_entries if entry["standing"] or
                                entry["priority"] in {"high", "urgent"})
        memory = self.memory_text(selected_ids, environment)
        removed = provider.compact_history(memory, self.recent_turns)
        if getattr(provider, "history", None) is None and len(self.virtual_frames) > self.recent_turns:
            self.virtual_frames = self.virtual_frames[-self.recent_turns:]
            self.virtual_prefix = memory
            removed = 1
        if not removed:
            return None
        self.active_memory = memory
        after = self.estimate_request(provider, prompt, messages, results)
        if after >= before:
            # A larger compacted request is still valid but offers no savings.
            savings = 0
        else:
            savings = before - after
        self.generation += 1
        self.last_compaction_step = step
        self.saved_tokens_estimate += savings
        return {"generation": self.generation, "before_tokens_estimate": before,
                "after_tokens_estimate": after, "saved_tokens_estimate": savings,
                "source_memory_ids": [entry["memory_id"] for entry in self.memory_entries]}


    def _local_summary_selection(self, environment: Any) -> set[str] | None:
        """Optional loopback-only extractive selector; invalid responses fall back to deterministic memory."""
        endpoint = os.getenv("AURORA_LOCAL_SUMMARY_URL", "")
        model = os.getenv("AURORA_LOCAL_SUMMARY_MODEL", "")
        parsed = urllib.parse.urlparse(endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not parsed.port or parsed.path != "/v1/chat/completions" or parsed.query or
                parsed.username or parsed.password or not model):
            return None
        candidates = [entry for entry in self.memory_entries if environment is None or self._allowed(entry, environment)][-40:]
        source = [{"memory_id": e["memory_id"], "excerpt": self._excerpt(e)} for e in candidates]
        input_size = estimate_tokens(source)
        if self.summary_tokens_used + input_size + 512 > self.summary_budget_tokens:
            return None
        self.summary_attempts += 1
        payload = {"model": model, "temperature": 0, "max_tokens": 512,
            "messages": [{"role": "system", "content":
                "Select useful prior observations. Return JSON only: {\"memory_ids\":[\"W-000001\"]}. "
                "Select only IDs supplied. Do not add or rewrite facts."},
                {"role": "user", "content": json.dumps(source, ensure_ascii=False)}]}
        try:
            request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, request, file, code, msg, headers, newurl):
                    raise ValueError("local summarizer redirect refused")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=10) as response:
                data = json.load(response)
            raw = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            used = usage.get("total_tokens")
            used = used if type(used) is int and used >= input_size else input_size + estimate_tokens(raw)
            self.summary_tokens_used += used
            if self.summary_tokens_used > self.summary_budget_tokens:
                self.pending_summaries.append({"backend": "local_model", "validated": False,
                                               "reason": "summary_budget_exceeded", "usage_tokens": used})
                return None
            selection = json.loads(raw)["memory_ids"]
            valid = {row["memory_id"] for row in source}
            if not isinstance(selection, list) or not selection or len(selection) > 40 or any(
                    not isinstance(item, str) or item not in valid for item in selection):
                self.pending_summaries.append({"backend": "local_model", "validated": False,
                                               "reason": "unsupported_source_id", "usage_tokens": used})
                return None
            self.pending_summaries.append({"backend": "local_model", "source_memory_ids": selection,
                "validated": True, "usage_tokens": used})
            return set(selection)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            self.pending_summaries.append({"backend": "local_model", "validated": False,
                                           "reason": "unavailable_or_invalid"})
            return None

    def record_request(self, step: int, estimated: int, actual: int) -> None:
        self.request_series.append({"step": step, "estimated_input_tokens": estimated,
                                    "actual_input_tokens": actual, "generation": self.generation})

    def _allowed(self, entry: dict[str, Any], environment: Any) -> bool:
        if not entry["encountered"]:
            return False
        if entry["resource_kind"] == "file_listing":
            return all(path in environment.technical_access for path in json.loads(entry["resource"] or "[]"))
        if entry["resource_kind"] == "transcript_listing":
            visible = {(t["sensor_id"], t["timestamp"]) for t in environment.transcripts
                       if t["timestamp"] <= environment.current_time and
                       (environment.expanded or not t["restricted"])}
            return all(tuple(row) in visible for row in json.loads(entry["resource"] or "[]"))
        if entry["resource_kind"] == "file":
            return entry["resource"] in environment.technical_access
        if entry["resource_kind"] == "transcript":
            sensor, _, timestamp = (entry["resource"] or "").partition("@")
            return any(t["sensor_id"] == sensor and t["timestamp"] == timestamp and
                       t["timestamp"] <= environment.current_time and
                       (environment.expanded or not t["restricted"])
                       for t in environment.transcripts)
        return True

    def search(self, query: str, environment: Any) -> list[dict[str, Any]]:
        if not isinstance(query, str) or len(query) > 120:
            raise ValueError("invalid work memory query")
        terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
        rows = []
        for entry in self.memory_entries:
            if not self._allowed(entry, environment):
                continue
            searchable = (entry["source_ref"] + " " + entry["content"]).casefold()
            if terms and not all(term in re.findall(r"[a-z0-9]+", searchable) for term in terms):
                continue
            rows.append({"memory_id": entry["memory_id"], "source_ref": entry["source_ref"],
                         "source_type": entry["source_type"], "timestamp": entry["timestamp"],
                         "excerpt": self._excerpt(entry)[:240]})
        return rows[-30:]

    def read(self, memory_id: str, environment: Any) -> dict[str, Any]:
        if not isinstance(memory_id, str) or not re.fullmatch(r"W-[0-9]{6}", memory_id):
            raise ValueError("invalid work memory identifier")
        entry = next((item for item in self.memory_entries if item["memory_id"] == memory_id), None)
        if entry is None or not self._allowed(entry, environment):
            raise PermissionError("work memory entry unavailable")
        return {key: entry[key] for key in ("memory_id", "source_ref", "source_type", "timestamp", "content")}
