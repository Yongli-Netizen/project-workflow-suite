import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "workflow.py"
SPEC = importlib.util.spec_from_file_location("project_workflow", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def plan():
    return {"version": 1, "goal": "test", "max_parallel": 1, "tasks": [{
        "id": "T1", "goal": "work", "depends_on": [], "write_paths": ["src/a.py"],
        "read_paths": [], "forbidden_paths": ["data"], "acceptance": ["done"],
        "risk": "low", "required_checks": ["diff", "codebase-memory"],
    }]}


def context():
    return {"objective": "finish", "current_phase": "implementation", "completed": [],
            "decisions": ["keep API"], "assumptions": [], "blockers": [],
            "next_actions": ["edit src/a.py"], "references": ["task:T1"],
            "updated_files": ["src/a.py"]}


def dispatch_state(definitions, max_parallel=4):
    tasks = {}
    for task_id, status, dependencies in definitions:
        tasks[task_id] = {
            "spec": {
                "id": task_id, "goal": f"goal {task_id}", "depends_on": dependencies,
                "write_paths": [f"src/{task_id}.py"], "read_paths": [],
                "forbidden_paths": ["data"], "acceptance": ["done"], "risk": "low",
                "required_checks": ["diff", "codebase-memory"],
            },
            "status": status, "token": f"secret-{task_id}" if status != "pending" else None,
            "owner": None, "agent": None, "attempt": 0, "approval": None,
            "checks": {}, "review": None,
        }
    return {"max_parallel": max_parallel, "tasks": tasks}


def retrieval_request(**updates):
    request = {
        "graph_available": True,
        "query_outcome": "hit",
        "intent": "lookup",
        "changed_files": [],
        "coverage_status": "clean",
        "requested_page_size": 25,
        "requested_source_paths": ["src/a.py"],
        "index_state": "ready",
    }
    request.update(updates)
    return request


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src/a.py").write_text("x = 1\n", encoding="utf-8")
        self.workflow = MODULE.Workflow(self.root)
        self.workflow.init(plan())

    def tearDown(self):
        self.tmp.cleanup()

    def test_checkpoint_rejects_stale_revision_and_wrong_owner(self):
        result = self.workflow.checkpoint("session-a", context(), 0)
        self.assertEqual(result["coordinator"], "session-a")
        with self.assertRaisesRegex(MODULE.WorkflowError, "revision changed"):
            self.workflow.checkpoint("session-a", context(), 0)
        with self.assertRaisesRegex(MODULE.WorkflowError, "active coordinator"):
            self.workflow.checkpoint("session-b", context(), result["revision"])

    def test_two_phase_handoff(self):
        saved = self.workflow.checkpoint("session-a", context(), 0)
        prepared = self.workflow.handoff_prepare("session-a", "session-b", context(), saved["revision"])
        self.assertNotIn("token", prepared["resume"]["handoff"])
        with self.assertRaisesRegex(MODULE.WorkflowError, "invalid handoff"):
            self.workflow.handoff_accept("session-c", prepared["handoff_token"])
        accepted = self.workflow.handoff_accept("session-b", prepared["handoff_token"])
        self.assertEqual(accepted["coordinator"], "session-b")
        self.assertIsNone(accepted["handoff"])
        self.assertEqual(accepted["context"]["document"]["objective"], "finish")

    def test_context_rejects_chat_dump_and_unsafe_path(self):
        oversized = context()
        oversized["decisions"] = ["x" * 4001]
        with self.assertRaises(MODULE.WorkflowError):
            MODULE.validate_context(oversized)
        unsafe = context()
        unsafe["updated_files"] = ["../secret"]
        with self.assertRaises(MODULE.WorkflowError):
            MODULE.validate_context(unsafe)

    def test_write_task_accepts_targeted_checks_without_codebase_memory(self):
        targeted = plan()
        targeted["tasks"][0]["required_checks"] = ["diff"]
        project = self.root / "new-project"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src/a.py").write_text("x = 1\n", encoding="utf-8")
        workflow = MODULE.Workflow(project)
        workflow.init(targeted)
        task = workflow.claim("T1", "root")
        workflow.bind("T1", task["token"], "root-agent")
        workflow.submit("T1", task["token"], {
            "summary": "done", "changed_files": [], "risks": [], "unresolved": []})
        workflow.check("T1", task["token"], "diff", [sys.executable, "-c", "print('ok')"])
        workflow.accept("T1", task["token"])
        self.assertEqual(workflow.status()["tasks"]["T1"]["status"], "done")

    def test_pressure_uses_observable_proxy_and_auto_signal(self):
        for index in range(5):
            self.workflow.approve("T1", f"ref-{index}")
        state = self.workflow.status()
        state["handoff_policy"] = {"mode": "auto", "soft_event_limit": 5, "hard_event_limit": 6,
                                   "authorization_reference": "user requested automatic handoff"}
        with self.workflow.transaction() as stored:
            stored["handoff_policy"] = state["handoff_policy"]
        self.assertEqual(self.workflow.pressure()["recommended_action"], "checkpoint")
        self.workflow.approve("T1", "ref-hard")
        pressure = self.workflow.pressure()
        self.assertEqual(pressure["recommended_action"], "handoff")
        self.assertTrue(pressure["auto_create_successor"])
        self.assertTrue(pressure["successor_creation_requested"])
        self.assertTrue(pressure["signal_only"])
        self.assertFalse(pressure["handoff_pending"])
        self.assertEqual(pressure["metric"], "workflow-events-not-model-token-percentage")
        capability = pressure["capability"]
        self.assertIsInstance(capability, dict)
        self.assertIs(capability["model_token_telemetry"], False)
        self.assertEqual(capability["pressure_source"], "workflow-events-since-checkpoint")
        self.assertEqual(capability["successor_creation"], "host-only")
        self.assertEqual(capability["controller_behavior"], "signal-only")

    def test_auto_handoff_requires_authorization_and_pending_handoff_suppresses_signal(self):
        unauthorized = plan()
        unauthorized["handoff_policy"] = {
            "mode": "auto", "soft_event_limit": 5, "hard_event_limit": 6,
        }
        project = self.root / "unauthorized"
        project.mkdir()
        with self.assertRaisesRegex(MODULE.WorkflowError, "explicit user authorization"):
            MODULE.Workflow(project).init(unauthorized)

        state = {
            "handoff_policy": {
                "mode": "auto", "soft_event_limit": 5, "hard_event_limit": 6,
                "authorization_reference": "user:automatic handoff approved",
            },
            "events": [{"revision": revision} for revision in range(1, 7)],
            "context": None,
            "handoff": {"successor": "session-b"},
        }
        pressure = MODULE.Workflow.context_pressure_from_state(state)
        self.assertEqual(pressure["recommended_action"], "handoff")
        self.assertTrue(pressure["handoff_pending"])
        self.assertFalse(pressure["successor_creation_requested"])
        self.assertFalse(pressure["auto_create_successor"])
        self.assertTrue(pressure["signal_only"])

    def test_scope_audit_rejects_undeclared_change(self):
        task = self.workflow.claim("T1", "root")
        self.workflow.bind("T1", task["token"], "worker")
        (self.root / "outside.txt").write_text("unexpected\n", encoding="utf-8")
        report = {"summary": "done", "changed_files": [], "risks": [], "unresolved": []}
        with self.assertRaisesRegex(MODULE.WorkflowError, "out-of-scope"):
            self.workflow.submit("T1", task["token"], report)

    def test_test_gate_requires_non_vacuous_structured_evidence(self):
        custom = plan()
        custom["tasks"][0]["required_checks"].append("tests")
        project = self.root / "test-gate"
        (project / "src").mkdir(parents=True)
        (project / "src/a.py").write_text("x = 1\n", encoding="utf-8")
        (project / "test_real_unittest.py").write_text(
            "import unittest\n"
            "class Demo(unittest.TestCase):\n"
            "    def test_real_assertion(self):\n"
            "        self.assertEqual(1 + 1, 2)\n",
            encoding="utf-8",
        )
        workflow = MODULE.Workflow(project)
        workflow.init(custom)
        task = workflow.claim("T1", "root")
        workflow.bind("T1", task["token"], "worker")
        (project / "src/a.py").write_text("x = 2\n", encoding="utf-8")
        report = {"summary": "done", "changed_files": ["src/a.py"], "risks": [], "unresolved": []}
        workflow.submit("T1", task["token"], report)
        invalid = {"kind": "tests", "framework": "demo", "executed": 0, "passed": 0,
                   "failed": 0, "skipped": 0,
                   "output_digest": MODULE.output_digest("Ran 0 tests\n")}
        with self.assertRaisesRegex(MODULE.WorkflowError, "no passing tests"):
            workflow.check("T1", task["token"], "tests",
                           [sys.executable, "-c", "print('Ran 0 tests')"], external_evidence=invalid)
        command = [sys.executable, "-m", "unittest", "-v", "test_real_unittest.py"]
        completed = subprocess.run(
            command, cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        self.assertEqual(completed.returncode, 0)
        valid_output = completed.stdout + completed.stderr
        valid = {"kind": "tests", "framework": "unittest", "executed": 1, "passed": 1,
                 "failed": 0, "skipped": 0,
                 "output_digest": MODULE.output_digest(valid_output)}
        quality = MODULE.validate_test_evidence(
            valid, workflow.status()["tasks"]["T1"]["spec"], valid_output, command)
        self.assertEqual(quality["executed"], 1)
        self.assertEqual(quality["output_digest"], MODULE.output_digest(valid_output))

    def test_test_gate_accepts_partial_skips_without_assertion_or_acceptance_claims(self):
        output = "1 passed, 1 skipped in 0.01s\n"
        evidence = {"kind": "tests", "framework": "pytest", "executed": 2, "passed": 1,
                    "failed": 0, "skipped": 1,
                    "output_digest": MODULE.output_digest(output)}
        validated = MODULE.validate_test_evidence(
            evidence, plan()["tasks"][0], output, [sys.executable, "-m", "pytest"])
        self.assertEqual(validated, evidence)

    def test_test_gate_rejects_print_only_command_despite_self_reported_counts(self):
        custom = plan()
        custom["tasks"][0]["required_checks"].append("tests")
        project = self.root / "spoofed-test-gate"
        (project / "src").mkdir(parents=True)
        (project / "src/a.py").write_text("x = 1\n", encoding="utf-8")
        workflow = MODULE.Workflow(project)
        workflow.init(custom)
        task = workflow.claim("T1", "root")
        workflow.bind("T1", task["token"], "worker")
        (project / "src/a.py").write_text("x = 2\n", encoding="utf-8")
        workflow.submit("T1", task["token"], {
            "summary": "done", "changed_files": ["src/a.py"], "risks": [], "unresolved": [],
        })
        spoofed_output = "1 passed\n"
        document = {"kind": "tests", "framework": "pytest", "executed": 1, "passed": 1,
                    "failed": 0, "skipped": 0,
                    "output_digest": MODULE.output_digest(spoofed_output)}
        with self.assertRaises(MODULE.WorkflowError):
            workflow.check("T1", task["token"], "tests",
                           [sys.executable, "-c", "print('1 passed')"], external_evidence=document)

    def test_test_gate_rejects_script_that_forges_unittest_summary(self):
        spec = plan()["tasks"][0]
        fake = self.root / "fake_unittest.py"
        fake.write_text("print('Ran 1 test')\nprint('OK')\n", encoding="utf-8")
        command = [sys.executable, str(fake)]
        completed = subprocess.run(
            command, cwd=self.root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        self.assertEqual(completed.returncode, 0)
        output = completed.stdout + completed.stderr
        document = {"kind": "tests", "framework": "unittest", "executed": 1, "passed": 1,
                    "failed": 0, "skipped": 0,
                    "output_digest": MODULE.output_digest(output)}
        with self.assertRaises(MODULE.WorkflowError):
            MODULE.validate_test_evidence(document, spec, output, command)

    def test_test_gate_rejects_evidence_from_different_command_output(self):
        spec = plan()["tasks"][0]
        document = {"kind": "tests", "framework": "demo", "executed": 1, "passed": 1,
                    "failed": 0, "skipped": 0,
                    "output_digest": MODULE.output_digest("1 passed\n")}
        with self.assertRaisesRegex(MODULE.WorkflowError, "does not match captured command output"):
            MODULE.validate_test_evidence(
                document, spec, "different output\n", [sys.executable, "tests.py"])

    def test_codebase_memory_evidence_is_bound_to_root_output_and_changed_files(self):
        task = {"report": {"changed_files": ["src/a.py"]}}
        output = "indexed generation 7\n"
        evidence = {
            "kind": "codebase-memory", "project": "demo",
            "repository_root": str(self.root), "status": "indexed", "generation": 7,
            "coverage": "indexed_no_recorded_gap", "coverage_checked": ["src/a.py"],
            "source_verified": ["src/a.py"], "gaps": [],
            "output_digest": MODULE.output_digest(output),
        }
        self.assertIs(MODULE.validate_memory_evidence(evidence, task, self.root, output), evidence)

        wrong_root = dict(evidence, repository_root=str(self.root / "src"))
        with self.assertRaisesRegex(MODULE.WorkflowError, "different repository root"):
            MODULE.validate_memory_evidence(wrong_root, task, self.root, output)

        missing_coverage = dict(evidence, coverage_checked=["docs/readme.md"])
        with self.assertRaisesRegex(MODULE.WorkflowError, "included in index coverage"):
            MODULE.validate_memory_evidence(missing_coverage, task, self.root, output)

        missing_source = dict(evidence, source_verified=["src/missing.py"])
        with self.assertRaisesRegex(MODULE.WorkflowError, "not a current repository file"):
            MODULE.validate_memory_evidence(missing_source, task, self.root, output)

        wrong_output = dict(evidence, output_digest=MODULE.output_digest("stale output\n"))
        with self.assertRaisesRegex(MODULE.WorkflowError, "does not match captured command output"):
            MODULE.validate_memory_evidence(wrong_output, task, self.root, output)

    def test_agent_summary_maps_active_and_done_tasks_without_tokens(self):
        expanded = plan()
        expanded["tasks"].extend([
            {
                "id": "T2", "goal": "review work", "depends_on": [],
                "write_paths": ["src/b.py"], "read_paths": [], "forbidden_paths": ["data"],
                "acceptance": ["reviewed"], "risk": "low",
                "required_checks": ["diff", "codebase-memory"],
            },
            {
                "id": "T3", "goal": "finished work", "depends_on": [],
                "write_paths": ["src/c.py"], "read_paths": [], "forbidden_paths": ["data"],
                "acceptance": ["finished"], "risk": "low",
                "required_checks": ["diff", "codebase-memory"],
            },
        ])
        for task_id, goal, filename in (
            ("T4", "pending work", "d.py"),
            ("T5", "blocked work", "e.py"),
            ("T6", "failed work", "f.py"),
        ):
            expanded["tasks"].append({
                "id": task_id, "goal": goal, "depends_on": [],
                "write_paths": [f"src/{filename}"], "read_paths": [], "forbidden_paths": ["data"],
                "acceptance": ["handled"], "risk": "low",
                "required_checks": ["diff", "codebase-memory"],
            })
        project = self.root / "agent-summary"
        (project / "src").mkdir(parents=True)
        for name in ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"):
            (project / "src" / name).write_text("x = 1\n", encoding="utf-8")
        workflow = MODULE.Workflow(project)
        workflow.init(expanded)
        with workflow.transaction() as state:
            state["tasks"]["T1"].update(status="running", agent="agent-run", token="secret-running", attempt=1)
            state["tasks"]["T2"].update(status="review", agent="agent-review", token="secret-review", attempt=2)
            state["tasks"]["T3"].update(status="done", agent="agent-done", token="secret-done", attempt=1)
            state["tasks"]["T4"].update(status="pending", agent=None, token=None, attempt=0)
            state["tasks"]["T5"].update(status="blocked", agent="agent-blocked", token="secret-blocked", attempt=2)
            state["tasks"]["T6"].update(status="failed", agent="agent-failed", token="secret-failed", attempt=3)

        summary = workflow.agent_summary()
        self.assertEqual(
            [(item["task_id"], item["goal"], item["agent"], item["status"])
             for item in summary["running"]],
            [("T1", "work", "agent-run", "running"),
             ("T2", "review work", "agent-review", "review")],
        )
        self.assertEqual(
            [(item["task_id"], item["goal"], item["agent"], item["status"])
             for item in summary["completed"]],
            [("T3", "finished work", "agent-done", "done")],
        )
        self.assertEqual(
            [(item["task_id"], item["goal"], item["agent"], item["status"])
             for item in summary["waiting"]],
            [("T4", "pending work", None, "pending"),
             ("T5", "blocked work", "agent-blocked", "blocked"),
             ("T6", "failed work", "agent-failed", "failed")],
        )
        self.assertEqual(summary["counts"], {"running": 2, "completed": 1, "waiting": 3})
        serialized = MODULE.encoded(summary)
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret-running", serialized)
        self.assertNotIn("secret-review", serialized)
        self.assertNotIn("secret-done", serialized)
        self.assertNotIn("secret-blocked", serialized)
        self.assertNotIn("secret-failed", serialized)

    def test_dispatch_plan_assigns_root_then_children_by_available_capacity(self):
        for ready_count, expected_root, expected_children in (
            (1, "T1", []),
            (2, "T1", ["T2"]),
            (3, "T1", ["T2", "T3"]),
        ):
            with self.subTest(ready_count=ready_count):
                state = dispatch_state([
                    (f"T{index}", "pending", []) for index in range(1, ready_count + 1)
                ])
                result = MODULE.Workflow.dispatch_plan_from_state(state, capacity=3)
                self.assertEqual(result["root_task"], expected_root)
                self.assertEqual(result["child_tasks"], expected_children)
                self.assertEqual(result["queued_tasks"], [])
                self.assertEqual(result["capacity"], 3)
                self.assertEqual(result["ready_count"], ready_count)
                self.assertIsInstance(result["reason"], str)
                self.assertTrue(result["reason"])
                self.assertNotIn("token", MODULE.encoded(result))

    def test_dispatch_plan_fills_one_wave_and_ignores_blocked_or_nonpending_tasks(self):
        overflow = dispatch_state([
            ("T4", "pending", []), ("T2", "pending", []),
            ("T1", "pending", []), ("T3", "pending", []),
        ])
        wave = MODULE.Workflow.dispatch_plan_from_state(overflow, capacity=2)
        self.assertEqual(wave["root_task"], "T1")
        self.assertEqual(wave["child_tasks"], ["T2"])
        self.assertEqual(wave["queued_tasks"], ["T3", "T4"])
        self.assertEqual(wave["ready_count"], 4)

        filtered = dispatch_state([
            ("T1", "done", []),
            ("T2", "pending", ["T1"]),
            ("T3", "pending", ["T4"]),
            ("T4", "running", []),
        ])
        result = MODULE.Workflow.dispatch_plan_from_state(filtered, capacity=4)
        self.assertEqual(result["root_task"], "T2")
        self.assertEqual(result["child_tasks"], [])
        self.assertEqual(result["queued_tasks"], [])
        self.assertNotIn("T1", MODULE.encoded(result))
        self.assertNotIn("T3", MODULE.encoded(result))
        self.assertNotIn("T4", MODULE.encoded(result))
        self.assertNotIn("secret-", MODULE.encoded(result))

    def test_dispatch_capacity_is_host_availability_bounded_by_workflow_parallel_limit(self):
        state = dispatch_state([
            ("T0", "running", []),
            ("T1", "pending", []), ("T2", "pending", []), ("T3", "pending", []),
        ], max_parallel=4)
        result = MODULE.Workflow.dispatch_plan_from_state(state, capacity=2)
        self.assertEqual(result["capacity"], 2)
        self.assertEqual(result["root_task"], "T1")
        self.assertEqual(result["child_tasks"], ["T2"])
        self.assertEqual(result["queued_tasks"], ["T3"])

        constrained = dispatch_state([
            ("A1", "running", []), ("A2", "running", []), ("A3", "running", []),
            ("T1", "pending", []), ("T2", "pending", []),
        ], max_parallel=4)
        limited = MODULE.Workflow.dispatch_plan_from_state(constrained, capacity=3)
        self.assertEqual(limited["capacity"], 1)
        self.assertEqual(limited["root_task"], "T1")
        self.assertEqual(limited["child_tasks"], [])
        self.assertEqual(limited["queued_tasks"], ["T2"])

    def test_dispatch_allows_zero_host_capacity_without_assigning(self):
        state = dispatch_state([("T1", "pending", []), ("T2", "pending", [])])
        result = MODULE.Workflow.dispatch_plan_from_state(state, capacity=0)
        self.assertEqual(result["capacity"], 0)
        self.assertEqual(result["root_task"], None)
        self.assertEqual(result["child_tasks"], [])
        self.assertEqual(result["queued_tasks"], ["T1", "T2"])
        self.assertEqual(result["reason"], "no-ready-capacity")

    def test_retrieval_policy_uses_graph_first_and_bounds_evidence_scope(self):
        paths = [f"src/p{index}.py" for index in range(30)]
        policy = MODULE.retrieval_policy(retrieval_request(
            requested_page_size=500, requested_source_paths=paths))
        self.assertEqual(policy["tier"], "scout")
        self.assertNotEqual(policy["scanning"], "full-repo")
        self.assertEqual(policy["scanning"], "none")
        self.assertEqual(policy["index_update"], "incremental")
        self.assertEqual(policy["query_budget"], {
            "max_queries": 3, "max_source_paths": 5, "page_size": 20,
        })
        self.assertEqual(policy["page_size"], 20)
        self.assertEqual(policy["source_paths"], paths[:5])
        self.assertIsInstance(policy["fallback_reasons"], list)

    def test_retrieval_policy_escalates_tiers_and_limits_text_fallback(self):
        self.assertEqual(MODULE.retrieval_policy(
            retrieval_request(query_outcome="miss"))["tier"], "verify")
        for intent in ("audit", "exhaustive", "negative"):
            with self.subTest(intent=intent):
                self.assertEqual(MODULE.retrieval_policy(
                    retrieval_request(intent=intent))["tier"], "auditor")

        coverage_gap = MODULE.retrieval_policy(retrieval_request(coverage_status="gap"))
        self.assertEqual(coverage_gap["scanning"], "targeted")
        self.assertNotEqual(coverage_gap["scanning"], "full-repo")
        config = MODULE.retrieval_policy(retrieval_request(intent="config"))
        self.assertEqual(config["scanning"], "targeted")

    def test_retrieval_query_budgets_expand_by_tier_but_remain_bounded(self):
        paths = [f"src/p{index}.py" for index in range(30)]
        verify = MODULE.retrieval_policy(retrieval_request(
            query_outcome="miss", requested_page_size=999, requested_source_paths=paths))
        self.assertEqual(verify["query_budget"], {
            "max_queries": 8, "max_source_paths": 12, "page_size": 20,
        })
        self.assertEqual(verify["page_size"], 20)
        self.assertEqual(verify["source_paths"], paths[:12])

        auditor = MODULE.retrieval_policy(retrieval_request(
            intent="audit", requested_page_size=999, requested_source_paths=paths))
        self.assertEqual(auditor["query_budget"], {
            "max_queries": 12, "max_source_paths": 20, "page_size": 20,
        })
        self.assertEqual(auditor["page_size"], 20)
        self.assertEqual(auditor["source_paths"], paths[:20])

    def test_auditor_retrieval_requires_an_explicit_bounded_source_scope(self):
        for intent in ("audit", "exhaustive", "negative"):
            with self.subTest(intent=intent):
                with self.assertRaises(MODULE.WorkflowError):
                    MODULE.retrieval_policy(retrieval_request(
                        intent=intent, requested_source_paths=[]))

    def test_retrieval_policy_uses_full_index_only_for_documented_recovery_states(self):
        for index_state in ("missing", "corrupt", "forced", "unrecoverable"):
            with self.subTest(index_state=index_state):
                self.assertEqual(MODULE.retrieval_policy(
                    retrieval_request(index_state=index_state))["index_update"], "full")
        self.assertEqual(MODULE.retrieval_policy(
            retrieval_request(index_state="ready"))["index_update"], "incremental")

    def test_retrieval_policy_rejects_ambiguous_request_shape(self):
        missing = retrieval_request()
        missing.pop("intent")
        with self.assertRaises(MODULE.WorkflowError):
            MODULE.retrieval_policy(missing)
        with self.assertRaises(MODULE.WorkflowError):
            MODULE.retrieval_policy(retrieval_request(unexpected=True))


if __name__ == "__main__":
    unittest.main()
