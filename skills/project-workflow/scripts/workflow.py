"""Development task ledger; native Codex tools execute agents, not this CLI."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid

UNSET = object()


class WorkflowError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


CONTEXT_FIELDS = {
    "objective", "current_phase", "completed", "decisions", "assumptions",
    "blockers", "next_actions", "references", "updated_files",
}


def validate_context(document):
    require(isinstance(document, dict), "context must be an object")
    require(set(document) == CONTEXT_FIELDS, "context fields must match the documented schema")
    require(nonempty(document["objective"]) and nonempty(document["current_phase"]),
            "context objective/current_phase required")
    for key in CONTEXT_FIELDS - {"objective", "current_phase"}:
        values = document[key]
        require(isinstance(values, list) and all(nonempty(v) for v in values),
                f"context.{key} must be a string list")
        require(len(values) <= 100, f"context.{key} exceeds 100 items")
    strings = [document["objective"], document["current_phase"]]
    strings.extend(v for key in CONTEXT_FIELDS - {"objective", "current_phase"} for v in document[key])
    require(all(len(value) <= 4000 for value in strings), "context item exceeds 4000 characters")
    for name in document["updated_files"]:
        relative(name)
    require(len(encoded(document).encode("utf-8")) <= 65536,
            "context exceeds 64 KiB; summarize instead of storing chat")
    return document


def relative(value):
    require(nonempty(value), "path must be nonempty")
    require("\\" not in value and ":" not in value, "use repository-relative POSIX paths")
    parts = value.split("/")
    require(not any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in parts), "absolute/traversal/ambiguous path rejected")
    require(not any(c in value for c in "*?[]"), "declare concrete files or directories, not globs")
    require(not any(ord(c) < 32 for c in value) and "~" not in value, "control characters/short path aliases rejected")
    require(not any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", p) for p in parts), "reserved device path")
    require(parts[0].casefold() not in (".git", ".workflow", ".codex", ".agents"), "protected path")
    return value


def overlaps(a, b):
    a, b = a.casefold(), b.casefold()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def validate_handoff_policy(policy):
    if policy is None:
        policy = {}
    require(isinstance(policy, dict), "handoff_policy must be an object")
    require(set(policy) <= {"mode", "soft_event_limit", "hard_event_limit", "authorization_reference"},
            "unsupported handoff_policy field")
    result = {"mode": policy.get("mode", "manual"),
              "soft_event_limit": policy.get("soft_event_limit", 40),
              "hard_event_limit": policy.get("hard_event_limit", 80),
              "authorization_reference": policy.get("authorization_reference")}
    require(result["mode"] in ("manual", "auto"), "handoff_policy.mode must be manual or auto")
    require(type(result["soft_event_limit"]) is int and type(result["hard_event_limit"]) is int,
            "handoff event limits must be integers")
    require(5 <= result["soft_event_limit"] < result["hard_event_limit"] <= 10000,
            "handoff event limits must satisfy 5 <= soft < hard <= 10000")
    if result["mode"] == "auto":
        require(nonempty(result["authorization_reference"]),
                "automatic handoff requires an explicit user authorization reference")
    return result


def output_digest(output):
    return hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()


def retrieval_policy(request):
    """Choose a bounded graph-first discovery policy without touching the repository."""
    required = {"graph_available", "query_outcome", "intent", "changed_files",
                "coverage_status", "requested_page_size", "requested_source_paths",
                "index_state"}
    require(isinstance(request, dict) and set(request) == required,
            "retrieval request fields must match the documented schema")
    require(type(request["graph_available"]) is bool, "graph_available must be boolean")
    require(request["query_outcome"] in ("hit", "miss"), "query_outcome must be hit or miss")
    require(request["intent"] in ("lookup", "negative", "exhaustive", "audit", "config",
                                   "literal", "non-code"), "unsupported retrieval intent")
    require(request["coverage_status"] in ("clean", "gap", "stale", "unknown"),
            "unsupported coverage status")
    require(request["index_state"] in ("ready", "stale", "changed", "missing", "corrupt",
                                        "forced", "unrecoverable"),
            "unsupported index state")
    require(isinstance(request["changed_files"], list) and
            all(nonempty(path) for path in request["changed_files"]), "changed_files must be a path list")
    require(isinstance(request["requested_source_paths"], list) and
            all(nonempty(path) for path in request["requested_source_paths"]),
            "requested_source_paths must be a path list")
    require(type(request["requested_page_size"]) is int and request["requested_page_size"] > 0,
            "requested_page_size must be positive")

    audit_intents = {"negative", "exhaustive", "audit"}
    if request["intent"] in audit_intents:
        tier = "auditor"
    elif (request["query_outcome"] == "miss" or request["changed_files"] or
          request["coverage_status"] != "clean"):
        tier = "verify"
    else:
        tier = "scout"
    require(tier != "auditor" or request["requested_source_paths"],
            "auditor retrieval requires an explicit bounded source scope")

    fallback_reasons = []
    targeted_intents = {"config", "literal", "non-code"}
    if request["intent"] in targeted_intents:
        fallback_reasons.append("non-structural-content")
    if request["coverage_status"] != "clean":
        fallback_reasons.append("coverage-" + request["coverage_status"])
    if not request["graph_available"]:
        fallback_reasons.append("graph-unavailable")
    scanning = "targeted" if fallback_reasons else "none"
    require(not request["graph_available"] or scanning != "full-repository",
            "full-repository scanning is forbidden while the graph is available")

    budgets = {"scout": (3, 5), "verify": (8, 12), "auditor": (12, 20)}
    max_queries, source_budget = budgets[tier]
    page_size = min(request["requested_page_size"], 20)
    full_states = {"missing", "corrupt", "forced", "unrecoverable"}
    return {"tier": tier, "scanning": scanning,
            "index_update": "full" if request["index_state"] in full_states else "incremental",
            "query_budget": {"max_queries": max_queries,
                             "max_source_paths": source_budget, "page_size": page_size},
            "page_size": page_size,
            "source_paths": request["requested_source_paths"][:source_budget],
            "fallback_reasons": fallback_reasons}


def test_runner_from_command(command):
    """Identify supported runners from argv tokens, never from printable text."""
    require(isinstance(command, (list, tuple)) and command and
            all(nonempty(token) for token in command), "test runner command required")
    executable = Path(command[0]).name.casefold()
    for suffix in (".exe", ".cmd", ".bat"):
        if executable.endswith(suffix):
            executable = executable[:-len(suffix)]
            break
    if executable in ("pytest", "py.test"):
        return "pytest"
    python_launcher = bool(re.fullmatch(r"(?:pythonw?|py)(?:\d+(?:\.\d+)*)?", executable))
    require(python_launcher and len(command) >= 3 and command[1] == "-m",
            "tests must invoke pytest/py.test directly or Python with -m pytest/-m unittest")
    module = command[2].casefold()
    require(module in ("pytest", "unittest"), "unsupported Python test runner module")
    return module


def parse_test_output(framework, output, command):
    """Extract runner-reported counts; never infer success from caller counts alone."""
    declared = framework.casefold()
    runner = test_runner_from_command(command)
    require((runner == "pytest" and declared in ("pytest", "py.test")) or
            (runner == "unittest" and declared in ("unittest", "python unittest")),
            "declared test framework does not match the invoked runner")

    if runner == "pytest":
        summaries = re.findall(
            r"(?im)^(?:=+\s*)?((?:\d+\s+(?:passed|failed|errors?|skipped)(?:,\s*|\s+))+)(?:in\s+[\d.]+s)?.*$",
            output)
        require(summaries, "pytest result summary not found in command output")
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        for number, label in re.findall(r"(\d+)\s+(passed|failed|errors?|skipped)", summaries[-1], re.I):
            key = "failed" if label.casefold().startswith(("fail", "error")) else label.casefold()
            counts[key] += int(number)
    else:
        ran = re.findall(r"(?im)^Ran\s+(\d+)\s+tests?\b", output)
        result = re.findall(r"(?im)^(OK|FAILED)(?:\s*\(([^)]*)\))?\s*$", output)
        require(ran and result, "unittest Ran/result summary not found in command output")
        executed = int(ran[-1])
        details = {key.casefold(): int(value) for key, value in
                   re.findall(r"(failures|errors|skipped)\s*=\s*(\d+)", result[-1][1], re.I)}
        failed = details.get("failures", 0) + details.get("errors", 0)
        skipped = details.get("skipped", 0)
        counts = {"passed": executed - failed - skipped, "failed": failed, "skipped": skipped}
    if runner != "unittest":
        executed = sum(counts.values())
    require(executed >= 0 and counts["passed"] >= 0, "runner reported invalid test counts")
    return {"executed": executed, **counts}


def validate_test_evidence(document, spec, output, command=None):
    require(isinstance(document, dict) and document.get("kind") == "tests", "structured tests evidence required")
    required = {"kind", "framework", "executed", "passed", "failed", "skipped", "output_digest"}
    require(set(document) == required,
            "tests evidence fields must match the documented schema")
    require(nonempty(document["framework"]), "tests evidence framework required")
    for key in ("executed", "passed", "failed", "skipped"):
        require(type(document[key]) is int and document[key] >= 0, f"tests evidence {key} must be a nonnegative integer")
    require(document["executed"] == document["passed"] + document["failed"] + document["skipped"],
            "tests evidence counts are inconsistent")
    require(document["executed"] > 0 and document["passed"] > 0, "test check executed no passing tests")
    require(document["failed"] == 0, "test check reports failures")
    require(document["skipped"] < document["executed"], "all tests were skipped")
    require(document["output_digest"] == output_digest(output),
            "tests evidence does not match captured command output")
    require(isinstance(command, (list, tuple)) and command,
            "test runner command required for structured evidence")
    observed = parse_test_output(document["framework"], output, command)
    for key in ("executed", "passed", "failed", "skipped"):
        require(document[key] == observed[key],
                f"tests evidence {key} does not match runner output")
    zero_markers = ("ran 0 tests", "no tests ran", "no tests found", "0 passing", "0 passed")
    require(not any(marker in output.casefold() for marker in zero_markers),
            "test output reports zero executed tests")
    return document


def validate_memory_evidence(document, task, repository_root, output):
    require(isinstance(document, dict) and document.get("kind") == "codebase-memory",
            "structured codebase-memory evidence required")
    required = {"kind", "project", "repository_root", "status", "generation", "coverage",
                "coverage_checked", "source_verified", "gaps", "output_digest"}
    require(set(document) == required,
            "codebase-memory evidence fields must match the documented schema")
    require(nonempty(document["project"]) and document["status"] == "indexed",
            "codebase-memory project must be indexed")
    generation = document["generation"]
    require((type(generation) is int and generation >= 0) or
            (nonempty(generation) and generation.casefold() not in ("none", "null", "unknown")),
            "codebase-memory generation required")
    try:
        evidence_root = Path(document["repository_root"]).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkflowError("invalid codebase-memory repository_root") from exc
    require(evidence_root == repository_root,
            "codebase-memory evidence belongs to a different repository root")
    require(document["output_digest"] == output_digest(output),
            "codebase-memory evidence does not match captured command output")
    require(document["coverage"] in ("indexed_no_recorded_gap", "fallback_verified"),
            "codebase-memory coverage was not verified")
    require(isinstance(document["gaps"], list) and not document["gaps"],
            "unresolved codebase-memory coverage gaps remain")
    coverage_checked = document["coverage_checked"]
    require(isinstance(coverage_checked, list) and all(nonempty(v) for v in coverage_checked),
            "coverage_checked must be a path list")
    for name in coverage_checked:
        relative(name)
    verified = document["source_verified"]
    require(isinstance(verified, list) and all(nonempty(v) for v in verified),
            "source_verified must be a path list")
    for name in verified:
        relative(name)
        path = repository_root / name
        require(path.is_file() and path.resolve().is_relative_to(repository_root),
                "source_verified entry is not a current repository file: " + name)
    changed = task.get("report", {}).get("changed_files", [])
    require(all(any(overlaps(name, item) for item in coverage_checked) for name in changed),
            "every changed file must be included in index coverage evidence")
    require(all(any(overlaps(name, item) for item in verified) for name in changed),
            "every changed file must be verified against real source")
    return document


def validate_plan(plan):
    require(isinstance(plan, dict) and type(plan.get("version")) is int and plan["version"] == 1, "plan.version must be 1")
    require(nonempty(plan.get("goal")), "plan.goal required")
    limit = plan.get("max_parallel", 3)
    require(type(limit) is int and 1 <= limit <= 32, "max_parallel must be 1..32; respect actual tool slots")
    validate_handoff_policy(plan.get("handoff_policy"))
    tasks = plan.get("tasks")
    require(isinstance(tasks, list) and tasks, "nonempty tasks required")
    ids = set()
    for task in tasks:
        require(isinstance(task, dict), "task must be object")
        tid = task.get("id")
        require(isinstance(tid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tid), "invalid task id")
        require(tid not in ids, "duplicate task id")
        ids.add(tid)
        require(nonempty(task.get("goal")), "task.goal required")
        for key in ("depends_on", "write_paths", "read_paths", "forbidden_paths", "acceptance", "required_checks"):
            values = task.get(key, [] if key == "read_paths" else None)
            require(isinstance(values, list) and all(nonempty(v) for v in values), f"{tid}.{key} must be string list")
            require(len(values) == len(set(values)), f"duplicate {key}")
        require(task["acceptance"] and task["required_checks"], "acceptance and required_checks cannot be empty")
        require(task.get("risk") in ("low", "approval-required"), "risk must be low or approval-required")
        for key in ("write_paths", "read_paths", "forbidden_paths"):
            for path in task.get(key, []):
                relative(path)
        require(not any(overlaps(w, f) for w in task["write_paths"] for f in task["forbidden_paths"]), "write/forbidden overlap")
    graph = {t["id"]: t["depends_on"] for t in tasks}
    seen, visiting = set(), set()

    def visit(tid):
        require(tid in graph, "missing dependency")
        require(tid not in visiting, "dependency cycle")
        if tid in seen:
            return
        visiting.add(tid)
        for dep in graph[tid]:
            visit(dep)
        visiting.remove(tid)
        seen.add(tid)

    for tid in graph:
        visit(tid)
    return plan


class Workflow:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.directory = self.root / ".workflow"
        require(not self.directory.is_symlink() and not self.directory.is_junction(), "state directory cannot be a link")
        self.db = self.directory / "state.sqlite3"
        require(not self.db.is_symlink(), "state database cannot be a symlink")

    def safe_path(self, name):
        name = relative(name)
        path = self.root / name
        current = path
        while current != self.root:
            require(not current.is_symlink() and not current.is_junction(), "symlink/junction scope rejected")
            current = current.parent
        require(path.resolve().is_relative_to(self.root), "path escapes root")
        return path

    def snapshot(self, spec):
        hashes = {}
        for name in sorted(set(spec["write_paths"] + spec.get("read_paths", []))):
            path = self.safe_path(name)
            if not path.exists():
                hashes[name] = None
            elif path.is_file():
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                hashes[name + "/"] = "directory"
                for base, dirs, files in os.walk(path):
                    dirs[:] = sorted(d for d in dirs if d not in (".git", ".workflow", "__pycache__", "node_modules"))
                    for entry in dirs + sorted(files):
                        child = Path(base) / entry
                        rel = child.relative_to(self.root).as_posix()
                        self.safe_path(rel)
                        if child.is_file():
                            hashes[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
        return digest(hashes)

    def repo_state(self):
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=self.root, capture_output=True, timeout=30, check=True,
            )
            entries = result.stdout.split(b"\0")
            entries_by_name = {}
            index = 0
            while index < len(entries) and entries[index]:
                entry = entries[index].decode("utf-8", errors="surrogateescape")
                status, name = entry[:2], entry[3:]
                entries_by_name.setdefault(name, []).append(status)
                index += 1
                if "R" in status or "C" in status:
                    if index < len(entries) and entries[index]:
                        original = entries[index].decode("utf-8", errors="surrogateescape")
                        entries_by_name.setdefault(original, []).append(status + "-source")
                        index += 1
            mode = "git"
        except (OSError, subprocess.SubprocessError):
            entries_by_name = {}
            for base, dirs, files in os.walk(self.root):
                dirs[:] = sorted(d for d in dirs if d not in (".git", ".workflow", "__pycache__", "node_modules"))
                for name in sorted(files):
                    entries_by_name[(Path(base) / name).relative_to(self.root).as_posix()] = ["tree"]
            mode = "tree"
        files = {}
        for name in sorted(entries_by_name):
            normalized = name.replace("\\", "/")
            if normalized.startswith((".git/", ".workflow/")):
                continue
            path = self.root / normalized
            if path.is_file():
                content = hashlib.sha256(path.read_bytes()).hexdigest()
                kind = "file"
            elif path.is_dir():
                # Git reports dirty submodules as directories. Preserve the status so a
                # clean -> dirty transition is not collapsed into None -> None.
                content = None
                kind = "directory"
            else:
                content = None
                kind = "missing"
            files[normalized] = {"status": sorted(entries_by_name[name]),
                                 "kind": kind, "sha256": content}
        return {"mode": mode, "files": files, "digest": digest(files)}

    @staticmethod
    def repo_changes(before, after):
        def comparable(old, new):
            # Version-1 ledgers stored only a hash (or None). Compare that legacy
            # value with the new record's content hash so an in-flight task is not
            # invalidated merely because the controller was upgraded. New claims
            # compare the complete status/kind/hash record and detect submodules.
            if isinstance(old, dict) and isinstance(new, dict):
                return old, new
            if isinstance(old, dict):
                return old.get("sha256"), new
            if isinstance(new, dict):
                return old, new.get("sha256")
            return old, new

        names = set(before["files"]) | set(after["files"])
        changed = []
        for name in names:
            old, new = comparable(before["files"].get(name), after["files"].get(name))
            if old != new:
                changed.append(name)
        return sorted(changed)

    def scope_audit(self, state, task):
        current = self.repo_state()
        changed = self.repo_changes(task["repo_baseline"], current)
        own = [name for name in changed if any(overlaps(name, path) for path in task["spec"]["write_paths"])]
        other_scopes = [path for other in state["tasks"].values()
                        if other is not task and other.get("token") for path in other["spec"]["write_paths"]]
        concurrent = [name for name in changed if name not in own and any(overlaps(name, path) for path in other_scopes)]
        violations = [name for name in changed if name not in own and name not in concurrent]
        return {"mode": current["mode"], "changed": changed, "owned": own,
                "concurrent": concurrent, "violations": violations, "current_digest": current["digest"]}

    @staticmethod
    def context_pressure_from_state(state):
        policy = state.get("handoff_policy", validate_handoff_policy(None))
        checkpoint_revision = state.get("context", {}).get("state_revision", 0) if state.get("context") else 0
        events_since = sum(event["revision"] > checkpoint_revision for event in state["events"])
        if events_since >= policy["hard_event_limit"]:
            level, action = "hard", "handoff"
        elif events_since >= policy["soft_event_limit"]:
            level, action = "soft", "checkpoint"
        else:
            level, action = "normal", "none"
        creation_signal = (policy["mode"] == "auto" and action == "handoff" and
                           not state.get("handoff"))
        return {"level": level, "recommended_action": action, "events_since_checkpoint": events_since,
                "soft_event_limit": policy["soft_event_limit"], "hard_event_limit": policy["hard_event_limit"],
                "proxy_ratio": min(events_since / policy["hard_event_limit"], 1.0),
                # Backward-compatible field: this is a host-facing request signal,
                # never a claim that this ledger created a Codex task itself.
                "auto_create_successor": creation_signal,
                "successor_creation_requested": creation_signal,
                "signal_only": True,
                "handoff_pending": bool(state.get("handoff")),
                "authorization_reference": policy.get("authorization_reference"),
                "metric": "workflow-events-not-model-token-percentage",
                "capability": {"model_token_telemetry": False,
                               "pressure_source": "workflow-events-since-checkpoint",
                               "successor_creation": "host-only",
                               "controller_behavior": "signal-only"}}

    def init(self, plan):
        validate_plan(plan)
        for spec in plan["tasks"]:
            self.snapshot(spec)
        self.directory.mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.db, timeout=10)) as con, con:
            con.execute("CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT body FROM ledger WHERE id=1").fetchone()
            if old:
                state = json.loads(old[0])
                require(state["root"] == str(self.root) and state["plan_hash"] == digest(plan), "existing workflow differs; do not overwrite")
                return state
            state = {"version": 1, "root": str(self.root), "goal": plan["goal"], "plan_hash": digest(plan),
                     "max_parallel": plan.get("max_parallel", 3), "revision": 0, "events": [], "tasks": {},
                     "context": None, "coordinator": None, "handoff": None,
                     "handoff_policy": validate_handoff_policy(plan.get("handoff_policy"))}
            for spec in plan["tasks"]:
                state["tasks"][spec["id"]] = {"spec": spec, "status": "pending", "token": None,
                    "owner": None, "agent": None, "attempt": 0, "approval": None, "checks": {}, "review": None}
            con.execute("INSERT INTO ledger VALUES (1, ?)", (encoded(state),))
            return state

    @contextmanager
    def transaction(self):
        require(self.db.is_file(), "workflow not initialized")
        con = sqlite3.connect(f"{self.db.as_uri()}?mode=rw", uri=True, timeout=10)
        try:
            con.execute("BEGIN IMMEDIATE")
            state = json.loads(con.execute("SELECT body FROM ledger WHERE id=1").fetchone()[0])
            require(state["version"] == 1 and state["root"] == str(self.root), "state version/root mismatch")
            yield state
            con.execute("UPDATE ledger SET body=? WHERE id=1", (encoded(state),))
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def status(self):
        require(self.db.is_file(), "workflow not initialized")
        with closing(sqlite3.connect(f"{self.db.as_uri()}?mode=ro", uri=True)) as con:
            state = json.loads(con.execute("SELECT body FROM ledger WHERE id=1").fetchone()[0])
        require(state["root"] == str(self.root), "state root mismatch")
        return state

    @staticmethod
    def context_status_from_state(state):
        handoff = state.get("handoff")
        if handoff:
            handoff = {key: value for key, value in handoff.items() if key != "token"}
        return {"root": state["root"], "goal": state["goal"], "revision": state["revision"],
                "coordinator": state.get("coordinator"), "handoff": handoff,
                "context": state.get("context"), "context_pressure": Workflow.context_pressure_from_state(state),
                "tasks": {tid: {"status": task["status"], "agent": task.get("agent"),
                                 "attempt": task["attempt"]}
                          for tid, task in state["tasks"].items()}}

    def context_status(self):
        return self.context_status_from_state(self.status())

    def pressure(self):
        return self.context_pressure_from_state(self.status())

    def agent_summary(self):
        """Return a token-free view of active and completed agent work."""
        state = self.status()

        def item(tid, task):
            spec = task.get("spec", {})
            return {"task_id": tid, "goal": spec.get("goal"),
                    "status": task.get("status"), "agent": task.get("agent"),
                    "attempt": task.get("attempt", 0)}

        running = [item(tid, task) for tid, task in state["tasks"].items()
                   if task.get("status") in ("running", "review")]
        completed = [item(tid, task) for tid, task in state["tasks"].items()
                     if task.get("status") == "done"]
        waiting = [item(tid, task) for tid, task in state["tasks"].items()
                   if task.get("status") in ("pending", "blocked", "failed")]
        running.sort(key=lambda entry: entry["task_id"])
        completed.sort(key=lambda entry: entry["task_id"])
        waiting.sort(key=lambda entry: entry["task_id"])
        return {"revision": state["revision"], "running": running,
                "completed": completed, "waiting": waiting,
                "counts": {"running": len(running), "completed": len(completed),
                           "waiting": len(waiting)}}

    @staticmethod
    def dispatch_plan_from_state(state, capacity=None):
        """Assign one ready task to Root and the remainder to child agents for this wave."""
        require(isinstance(state, dict) and isinstance(state.get("tasks"), dict),
                "workflow state with tasks required")
        configured = state.get("max_parallel", 1)
        require(type(configured) is int and configured > 0, "max_parallel must be positive")
        if capacity is None:
            capacity = configured
        require(type(capacity) is int and capacity >= 0, "capacity must be nonnegative")
        holders = [task for task in state["tasks"].values() if task.get("token")]
        available = max(0, min(capacity, configured - len(holders)))

        def locally_ready(tid, task):
            if task.get("status") != "pending":
                return False
            spec = task.get("spec", {})
            if spec.get("risk") == "approval-required" and not task.get("approval"):
                return False
            return all(state["tasks"].get(dep, {}).get("status") == "done"
                       for dep in spec.get("depends_on", []))

        ready = sorted(tid for tid, task in state["tasks"].items() if locally_ready(tid, task))

        def scopes_conflict(left, right):
            ls, rs = left.get("spec", {}), right.get("spec", {})
            lw, lr = ls.get("write_paths", []), ls.get("read_paths", [])
            rw, rr = rs.get("write_paths", []), rs.get("read_paths", [])
            return (any(overlaps(a, b) for a in lw for b in rw + rr) or
                    any(overlaps(a, b) for a in lr for b in rw))

        selected, queued = [], []
        active = holders[:]
        for tid in ready:
            task = state["tasks"][tid]
            if len(selected) >= available or any(scopes_conflict(task, other) for other in active):
                queued.append(tid)
            else:
                selected.append(tid)
                active.append(task)
        if not selected:
            reason = "no-ready-capacity" if ready else "no-ready-tasks"
        elif len(selected) == 1:
            reason = "single-task-root-only"
        else:
            reason = "parallel-wave"
        return {"root_task": selected[0] if selected else None,
                "child_tasks": selected[1:], "queued_tasks": queued,
                "capacity": available, "ready_count": len(ready), "reason": reason}

    def dispatch_plan(self, capacity=None):
        return self.dispatch_plan_from_state(self.status(), capacity)

    def checkpoint(self, owner, document, expected_revision):
        require(nonempty(owner), "owner required")
        validate_context(document)
        with self.transaction() as state:
            require(state["revision"] == expected_revision, "state revision changed; reload before checkpoint")
            require(state.get("coordinator") in (None, owner),
                    "only the active coordinator may checkpoint")
            require(not state.get("handoff"), "handoff pending; accept or cancel it first")
            state["coordinator"] = owner
            state["context"] = {"document": document, "saved_at": time.time(),
                                "state_revision": state["revision"] + 1,
                                "digest": digest(document)}
            self.event(state, "@workflow", "context-checkpointed", owner=owner,
                       digest=state["context"]["digest"])
            return self.context_status_from_state(state)

    def handoff_prepare(self, owner, successor, document, expected_revision):
        require(nonempty(owner) and nonempty(successor) and owner != successor,
                "distinct owner/successor required")
        validate_context(document)
        with self.transaction() as state:
            require(state["revision"] == expected_revision, "state revision changed; reload before handoff")
            require(state.get("coordinator") in (None, owner),
                    "only the active coordinator may hand off")
            require(not state.get("handoff"), "handoff already pending")
            state["coordinator"] = owner
            state["context"] = {"document": document, "saved_at": time.time(),
                                "state_revision": state["revision"] + 1,
                                "digest": digest(document)}
            token = uuid.uuid4().hex
            state["handoff"] = {"from": owner, "to": successor, "token": token,
                                "prepared_at": time.time(), "context_digest": state["context"]["digest"]}
            self.event(state, "@workflow", "handoff-prepared", owner=owner, successor=successor,
                       context_digest=state["context"]["digest"])
            return {"handoff_token": token, "resume": self.context_status_from_state(state)}

    def handoff_accept(self, successor, token):
        require(nonempty(successor) and nonempty(token), "successor/token required")
        with self.transaction() as state:
            handoff = state.get("handoff")
            require(handoff and handoff["to"] == successor and handoff["token"] == token,
                    "stale or invalid handoff token")
            require(state.get("context", {}).get("digest") == handoff["context_digest"],
                    "handoff context changed")
            previous = state.get("coordinator")
            state["coordinator"] = successor
            state["handoff"] = None
            self.event(state, "@workflow", "handoff-accepted", previous=previous, successor=successor)
            return self.context_status_from_state(state)

    def handoff_cancel(self, owner, token):
        require(nonempty(owner) and nonempty(token), "owner/token required")
        with self.transaction() as state:
            handoff = state.get("handoff")
            require(handoff and handoff["from"] == owner and handoff["token"] == token,
                    "stale or invalid handoff token")
            state["handoff"] = None
            self.event(state, "@workflow", "handoff-cancelled", owner=owner)
            return self.context_status_from_state(state)

    def extend(self, plan):
        validate_plan(plan)
        with self.transaction() as state:
            require(plan["goal"] == state["goal"] and plan.get("max_parallel", 3) == state["max_parallel"], "keep workflow goal and concurrency unchanged")
            require(validate_handoff_policy(plan.get("handoff_policy")) == state.get("handoff_policy"),
                    "keep handoff policy unchanged")
            incoming = {spec["id"]: spec for spec in plan["tasks"]}
            for tid, task in state["tasks"].items():
                require(incoming.get(tid) == task["spec"], "existing tasks cannot be rewritten or removed")
            new_specs = [spec for tid, spec in incoming.items() if tid not in state["tasks"]]
            self.validate_memory_plan(new_specs)
            for tid, spec in incoming.items():
                self.snapshot(spec)
                if tid not in state["tasks"]:
                    state["tasks"][tid] = {"spec": spec, "status": "pending", "token": None,
                        "owner": None, "agent": None, "attempt": 0, "approval": None, "checks": {}, "review": None}
                    self.event(state, tid, "added")
            state["plan_hash"] = digest(plan)
            return state

    @staticmethod
    def event(state, tid, action, **details):
        state["revision"] += 1
        state["events"].append({"revision": state["revision"], "time": time.time(), "task": tid,
                                "action": action, **details})

    @staticmethod
    def task(state, tid, token=UNSET):
        require(tid in state["tasks"], "unknown task")
        task = state["tasks"][tid]
        if token is not UNSET:
            require(nonempty(token) and task["token"] is not None and task["token"] == token, "stale or invalid claim token")
        return task

    def reasons(self, state, tid):
        task = self.task(state, tid)
        reasons = []
        if task["status"] != "pending":
            reasons.append("not pending")
        if task["spec"]["risk"] == "approval-required" and not task["approval"]:
            reasons.append("user approval required")
        if any(state["tasks"][dep]["status"] != "done" for dep in task["spec"]["depends_on"]):
            reasons.append("dependencies not done")
        holders = [v for v in state["tasks"].values() if v["token"]]
        if len(holders) >= state["max_parallel"]:
            reasons.append("parallel limit reached")
        for other in holders:
            writes, reads = task["spec"]["write_paths"], task["spec"].get("read_paths", [])
            ow, ore = other["spec"]["write_paths"], other["spec"].get("read_paths", [])
            if any(overlaps(a, b) for a in writes for b in ow + ore) or any(overlaps(a, b) for a in reads for b in ow):
                reasons.append("scope reserved by " + other["spec"]["id"])
        return reasons

    def ready(self):
        state = self.status()
        return {tid: self.reasons(state, tid) for tid in state["tasks"]}

    def approve(self, tid, reference):
        require(nonempty(reference), "actual user approval reference required")
        with self.transaction() as state:
            task = self.task(state, tid)
            require(task["status"] == "pending", "approval requires pending task")
            task["approval"] = reference
            self.event(state, tid, "approval-recorded", reference=reference)

    def claim(self, tid, owner):
        require(nonempty(owner), "owner required")
        with self.transaction() as state:
            reasons = self.reasons(state, tid)
            require(not reasons, "; ".join(reasons))
            task = self.task(state, tid)
            task.update(status="running", token=uuid.uuid4().hex, owner=owner, agent=None,
                        attempt=task["attempt"] + 1, checks={}, review=None, report=None)
            task["baseline"] = self.snapshot(task["spec"])
            task["repo_baseline"] = self.repo_state()
            self.event(state, tid, "claimed", owner=owner, attempt=task["attempt"])
            return task

    def bind(self, tid, token, agent):
        require(nonempty(agent), "actual agent or root identity required")
        with self.transaction() as state:
            task = self.task(state, tid, token)
            require(task["status"] == "running", "bind requires running")
            require(task["agent"] in (None, agent), "agent already bound")
            task["agent"] = agent
            self.event(state, tid, "bound", agent=agent)

    def submit(self, tid, token, report):
        require(isinstance(report, dict) and nonempty(report.get("summary")), "report.summary required")
        for key in ("changed_files", "risks", "unresolved"):
            require(isinstance(report.get(key), list) and all(nonempty(v) for v in report[key]), f"report.{key} required")
        with self.transaction() as state:
            task = self.task(state, tid, token)
            require(task["status"] in ("running", "review") and task["agent"], "bind agent before submitting")
            for name in report["changed_files"]:
                self.safe_path(name)
                require(any(name.casefold() == p.casefold() or name.casefold().startswith(p.casefold() + "/")
                            for p in task["spec"]["write_paths"]), "reported file outside write scope")
            scope = self.scope_audit(state, task)
            require(not scope["violations"], "out-of-scope repository changes: " + ", ".join(scope["violations"]))
            task.update(status="review", report=report, submitted_digest=self.snapshot(task["spec"]), checks={}, review=None)
            task["scope_audit"] = scope
            self.event(state, tid, "submitted", digest=task["submitted_digest"], report=report)
            return task["submitted_digest"]

    def check(self, tid, token, name, command, timeout=60, external_evidence=None):
        state = self.status()
        task = self.task(state, tid, token)
        require(task["status"] == "review", "check requires review status")
        require(name in task["spec"]["required_checks"], "check not in plan")
        require(command and all(nonempty(v) for v in command), "explicit command argv required")
        before = self.snapshot(task["spec"])
        require(before == task["submitted_digest"], "sources changed; resubmit and rerun checks")
        started = time.time()
        # Commands come from Root, never automatically from an untrusted task report.
        try:
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout, shell=False)
            code, output = result.returncode, result.stdout + result.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, output = -1, str(exc)
        evidence = {"command": command, "exit_code": code, "output": output[-16000:], "started": started,
                    "finished": time.time(), "digest": before,
                    "output_digest": output_digest(output),
                    "unchanged": self.snapshot(task["spec"]) == before}
        if name == "tests" and code == 0 and evidence["unchanged"]:
            evidence["quality"] = validate_test_evidence(
                external_evidence, task["spec"], output, command)
        elif name == "codebase-memory" and code == 0 and evidence["unchanged"]:
            evidence["knowledge"] = validate_memory_evidence(
                external_evidence, task, self.root, output)
        with self.transaction() as state:
            current = self.task(state, tid, token)
            require(current["status"] == "review" and current["submitted_digest"] == before, "task changed during check")
            current["checks"][name] = evidence
            current["review"] = None
            self.event(state, tid, "checked", name=name, evidence=evidence)
        return evidence

    def review(self, tid, token, report):
        require(isinstance(report, dict), "review object required")
        require(nonempty(report.get("reviewer")) and nonempty(report.get("summary")), "reviewer and summary required")
        require(report.get("verdict") in ("pass", "fail"), "review verdict required")
        with self.transaction() as state:
            task = self.task(state, tid, token)
            require(task["status"] == "review", "review requires submitted task")
            require(report["reviewer"] not in (task["agent"], task["owner"]), "independent reviewer required")
            require(report.get("digest") == task["submitted_digest"] == self.snapshot(task["spec"]), "review digest stale")
            task["review"] = report
            self.event(state, tid, "reviewed", report=report)

    def accept(self, tid, token):
        with self.transaction() as state:
            task = self.task(state, tid, token)
            require(task["status"] == "review", "accept requires review status")
            require(not task["report"]["unresolved"], "unresolved work remains")
            current = self.snapshot(task["spec"])
            require(current == task["submitted_digest"], "sources changed; resubmit")
            scope = self.scope_audit(state, task)
            require(not scope["violations"], "out-of-scope repository changes: " + ", ".join(scope["violations"]))
            for name in task["spec"]["required_checks"]:
                check = task["checks"].get(name, {})
                require(check.get("exit_code") == 0 and check.get("unchanged") and check.get("digest") == current,
                        "missing, failed or stale check: " + name)
            review = task["review"]
            if review is not None:
                require(review.get("verdict") == "pass" and review.get("digest") == current,
                        "recorded independent review must pass")
            task.update(status="done", token=None)
            self.event(state, tid, "accepted", digest=current)

    def suspend(self, tid, token, status, reason):
        require(status in ("blocked", "failed") and nonempty(reason), "status/reason required")
        with self.transaction() as state:
            task = self.task(state, tid, token)
            require(task["status"] in ("running", "review"), "cannot suspend current state")
            task.update(status=status, reason=reason)
            self.event(state, tid, status, reason=reason)

    def retry(self, tid, token, stopped_reference):
        require(nonempty(stopped_reference), "reference proving executor stopped required")
        with self.transaction() as state:
            task = self.task(state, tid, token)
            require(task["status"] in ("blocked", "failed"), "suspend before retry")
            require(task["attempt"] < 3, "three attempts exhausted; reassess scope with user")
            task.update(status="pending", token=None, checks={}, review=None)
            self.event(state, tid, "retry-authorized", stopped_reference=stopped_reference)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("init", "validate", "extend"):
        sub.add_parser(action).add_argument("--plan", required=True)
    for action in ("status", "ready", "context", "pressure", "agent-summary"):
        sub.add_parser(action)
    p = sub.add_parser("dispatch-plan")
    p.add_argument("--capacity", type=int)
    p = sub.add_parser("retrieval-policy")
    p.add_argument("--request", required=True)
    p = sub.add_parser("checkpoint")
    p.add_argument("--owner", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--expected-revision", type=int, required=True)
    p = sub.add_parser("handoff-prepare")
    p.add_argument("--owner", required=True)
    p.add_argument("--successor", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--expected-revision", type=int, required=True)
    p = sub.add_parser("handoff-accept")
    p.add_argument("--successor", required=True)
    p.add_argument("--handoff-token", required=True)
    p = sub.add_parser("handoff-cancel")
    p.add_argument("--owner", required=True)
    p.add_argument("--handoff-token", required=True)
    p = sub.add_parser("claim")
    p.add_argument("task")
    p.add_argument("--owner", required=True)
    p = sub.add_parser("approve")
    p.add_argument("task")
    p.add_argument("--reference", required=True)
    for action in ("bind", "submit", "check", "review", "accept", "suspend", "retry"):
        p = sub.add_parser(action)
        p.add_argument("task")
        p.add_argument("--token", required=True)
        if action == "bind":
            p.add_argument("--agent", required=True)
        elif action in ("submit", "review"):
            p.add_argument("--report", required=True)
        elif action == "check":
            p.add_argument("--name", required=True)
            p.add_argument("--timeout", type=int, default=60)
            p.add_argument("--evidence")
        elif action == "suspend":
            p.add_argument("--status", choices=("blocked", "failed"), required=True)
            p.add_argument("--reason", required=True)
        elif action == "retry":
            p.add_argument("--stopped-reference", required=True)
    import sys
    raw = sys.argv[1:]
    command = raw[raw.index("--") + 1:] if "--" in raw else []
    args = parser.parse_args(raw[:raw.index("--")] if "--" in raw else raw)
    try:
        workflow = Workflow(args.root)
        load = lambda file: json.loads(Path(file).read_text(encoding="utf-8-sig"))
        action = args.action
        if action == "validate":
            result = validate_plan(load(args.plan))
        elif action in ("init", "extend"):
            result = getattr(workflow, action)(load(args.plan))
        elif action in ("status", "ready", "pressure"):
            result = getattr(workflow, action)()
        elif action == "context":
            result = workflow.context_status()
        elif action == "agent-summary":
            result = workflow.agent_summary()
        elif action == "dispatch-plan":
            result = workflow.dispatch_plan(args.capacity)
        elif action == "retrieval-policy":
            result = retrieval_policy(load(args.request))
        elif action == "checkpoint":
            result = workflow.checkpoint(args.owner, load(args.context), args.expected_revision)
        elif action == "handoff-prepare":
            result = workflow.handoff_prepare(args.owner, args.successor, load(args.context), args.expected_revision)
        elif action == "handoff-accept":
            result = workflow.handoff_accept(args.successor, args.handoff_token)
        elif action == "handoff-cancel":
            result = workflow.handoff_cancel(args.owner, args.handoff_token)
        elif action == "claim":
            result = workflow.claim(args.task, args.owner)
        elif action == "approve":
            result = workflow.approve(args.task, args.reference)
        elif action == "bind":
            result = workflow.bind(args.task, args.token, args.agent)
        elif action in ("submit", "review"):
            result = getattr(workflow, action)(args.task, args.token, load(args.report))
        elif action == "check":
            evidence = load(args.evidence) if args.evidence else None
            result = workflow.check(args.task, args.token, args.name, command, args.timeout, evidence)
        elif action == "accept":
            result = workflow.accept(args.task, args.token)
        elif action == "suspend":
            result = workflow.suspend(args.task, args.token, args.status, args.reason)
        else:
            result = workflow.retry(args.task, args.token, args.stopped_reference)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2))
        if action == "check" and (result["exit_code"] != 0 or not result["unchanged"]):
            return 1
        return 0
    except (WorkflowError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
