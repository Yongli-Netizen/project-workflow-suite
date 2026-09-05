import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("amazon_requirements_discovery", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


PHASES = [
    "IDEA",
    "DISCOVERY",
    "DATA_VALIDATION",
    "SOLUTION_OPTIONS",
    "PROTOTYPE_VALIDATION",
    "REQUIREMENTS_REVIEW",
    "APPROVED",
]
CORE_CATEGORIES = (
    "business-goal", "success-metric", "user-workflow", "scope",
    "automation-authority",
)
RESPONSE_MODES = [
    "accept-recommendation", "request-recommendation", "defer", "custom",
]


def option(option_id="recommended"):
    return {
        "id": option_id,
        "label": "复用现有客户端",
        "advantages": ["交付快"],
        "disadvantages": ["受现有接口约束"],
        "tradeoffs": ["用较小改动换取较少定制能力"],
        "recommendation_reason": "符合最小修改原则",
        "recommended": option_id == "recommended",
        "cost_effort": "低，约一个开发日",
        "risks": ["受现有接口约束"],
        "reusable_capabilities": ["AdsReportClient"],
        "new_work": ["增加最小适配配置"],
    }


def question(question_id="Q1", category="scope"):
    return {
        "id": question_id,
        "category": category,
        "prompt": "第一版采用哪种实现？",
        "why_now": "该决定影响实现边界",
        "response_modes": list(RESPONSE_MODES),
        "options": [option(), option("custom")],
    }


def capability(capability_id="C-base", status="reusable", repository_root=""):
    root = Path(repository_root).resolve() if repository_root else None
    source_path = root / "src/ads_report.py" if root else None
    if source_path:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        if not source_path.exists():
            source_path.write_text("class AdsReportClient:\n    pass\n", encoding="utf-8")
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    else:
        source_hash = hashlib.sha256(b"").hexdigest()
    return {
        "id": capability_id, "name": "AdsReportClient", "status": status,
        "reuse": "直接复用", "symbols": ["AdsReportClient"],
        "paths": ["src/ads_report.py"], "graph_project": "test-project",
        "repository_root": str(Path(repository_root).resolve()) if repository_root else "",
        "generation": "generation-1", "tier": "verify", "scope": "src",
        "pagination_complete": True,
        "relationships_checked": ["imports", "calls"],
        "mcp_output_digest": hashlib.sha256(b"bounded-mcp-output").hexdigest(),
        "coverage_checked": [{
            "path": "src/ads_report.py", "status": "no-recorded-gap"
        }],
        "source_verified": ["src/ads_report.py"],
        "source_hashes": {"src/ads_report.py": source_hash},
    }


def data_source(data_id="D-base", status="verified", blocking=True):
    sample = {
        "kind": "sample", "reference": "report-request-20260906",
        "digest": hashlib.sha256(b"redacted-report-sample").hexdigest(),
        "result": "成功读取脱敏广告报表样例",
    }
    permission_proof = {
        "kind": "permission-proof", "reference": "ads-api-scope-check-20260906",
        "digest": hashlib.sha256(b"redacted-permission-proof").hexdigest(),
        "result": "测试账号具备只读报表权限",
    }
    return {
        "id": data_id, "name": "广告报表", "status": status,
        "source": "Ads API", "boundary": "只读取脱敏测试账号", "blocking": blocking,
        "permission_status": "verified" if status == "verified" else "unknown",
        "validation_method": "读取脱敏测试账号样例" if status == "verified" else "待验证",
        "validation_evidence": [sample, permission_proof] if status == "verified" else [],
    }


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.discovery = MODULE.Discovery(self.root)
        self.discovery.init("PPC 建议系统", "帮助运营人员审核广告建议")

    def tearDown(self):
        self.tmp.cleanup()

    def assert_phase(self, phase):
        self.assertEqual(self.discovery.status()["phase"], phase)

    def valid_capability(self, capability_id="C-base", status="reusable"):
        return capability(capability_id, status, self.root)

    @staticmethod
    def confirmation(discovery):
        return {
            "source": "user-message",
            "reference": "user-confirmed-requirements-v1",
            "baseline_revision": discovery.status()["revision"],
            "confirmed": True,
        }

    @staticmethod
    def answer_core_questions(discovery):
        questions = [question(f"CORE-{index}", category) for index, category in enumerate(CORE_CATEGORIES)]
        discovery.ask(questions[:3])
        discovery.ask(questions[3:])
        for item in questions:
            discovery.answer(item["id"], choice_id="recommended", disposition="confirmed")

    def advance_to_review(self):
        state = self.discovery.status()
        if not state["capabilities"]:
            self.discovery.record_capability(self.valid_capability())
        if not state["data_sources"]:
            self.discovery.record_data(data_source())
        for phase in PHASES[1:-1]:
            if PHASES.index(phase) > PHASES.index(self.discovery.status()["phase"]):
                self.discovery.advance(phase)

    def test_init_creates_one_project_document_and_machine_state(self):
        self.assert_phase("IDEA")
        self.assertTrue((self.root / ".workflow/amazon-requirements.json").is_file())
        self.assertTrue((self.root / "docs/PROJECT.md").is_file())
        markdown_files = list(self.root.rglob("*.md"))
        self.assertEqual(markdown_files, [self.root / "docs/PROJECT.md"])
        document = (self.root / "docs/PROJECT.md").read_text(encoding="utf-8")
        self.assertIn("PPC 建议系统", document)
        self.assertIn("IDEA", document)

    def test_phase_transition_is_strictly_sequential(self):
        error = MODULE.DiscoveryError
        with self.assertRaises(error):
            self.discovery.advance("DATA_VALIDATION")
        with self.assertRaises(error):
            self.discovery.advance("APPROVED")

        self.discovery.record_capability(self.valid_capability())
        self.discovery.record_data(data_source())
        for phase in PHASES[1:-1]:
            self.discovery.advance(phase)
            self.assert_phase(phase)

        with self.assertRaises(error):
            self.discovery.advance("APPROVED")
        self.assert_phase("REQUIREMENTS_REVIEW")

    def test_question_batch_and_option_contract_are_enforced(self):
        error = MODULE.DiscoveryError
        with self.assertRaises(error):
            self.discovery.ask([question(f"Q{number}") for number in range(4)])

        for missing in (
            "label", "advantages", "disadvantages", "tradeoffs",
            "recommendation_reason", "cost_effort", "risks",
            "reusable_capabilities", "new_work",
        ):
            invalid = option()
            invalid.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(error):
                self.discovery.ask([{
                    "id": f"Q-{missing}", "category": "scope", "prompt": "选择？",
                    "why_now": "影响范围", "response_modes": list(RESPONSE_MODES),
                    "options": [invalid, option("other")],
                }])

        invalid_modes = question("Q-modes")
        invalid_modes["response_modes"] = RESPONSE_MODES[:-1]
        with self.assertRaises(error):
            self.discovery.ask([invalid_modes])

        for field in (
            "advantages", "disadvantages", "risks", "reusable_capabilities",
            "new_work", "tradeoffs",
        ):
            invalid = question(f"Q-empty-{field}")
            invalid["options"][0][field] = []
            with self.subTest(empty_option_field=field), self.assertRaises(error):
                self.discovery.ask([invalid])

        self.discovery.ask([question()])
        open_questions = [
            item for item in self.discovery.status()["questions"] if item["status"] == "open"
        ]
        self.assertEqual(len(open_questions), 1)

    def test_all_decision_dispositions_are_preserved(self):
        dispositions = (
            "confirmed", "recommended", "assumed", "unknown", "rejected", "deferred"
        )
        for index, disposition in enumerate(dispositions):
            item = question(f"Q{index}")
            item["options"][0]["id"] = f"O{index}"
            self.discovery.ask([item])
            kwargs = {"disposition": disposition, "choice_id": f"O{index}"}
            self.discovery.answer(f"Q{index}", **kwargs)

        decisions = self.discovery.status()["decisions"]
        self.assertEqual({item["disposition"] for item in decisions}, set(dispositions))
        document = (self.root / "docs/PROJECT.md").read_text(encoding="utf-8")
        for disposition in dispositions:
            self.assertIn(disposition, document)

    def test_capability_status_vocabulary_and_missing_evidence_gate(self):
        error = MODULE.DiscoveryError
        allowed = (
            "implemented", "reusable", "partial", "adapter-required",
            "missing", "unknown", "deprecated",
        )
        for index, status in enumerate(allowed):
            evidence = self.valid_capability(f"C{index}", status)
            evidence["name"] = f"capability-{index}"
            if status == "missing":
                evidence.update(tier="auditor")
            self.discovery.record_capability(evidence)

        with self.assertRaises(error):
            self.discovery.record_capability({"id": "bad", "name": "bad", "status": "ready"})
        for field, invalid in (
            ("tier", "verify"), ("generation", ""),
            ("pagination_complete", False), ("relationships_checked", []),
            ("coverage_checked", []), ("source_verified", []),
        ):
            evidence = self.valid_capability(f"missing-{field}", "missing")
            evidence["tier"] = "auditor"
            evidence[field] = invalid
            with self.subTest(field=field), self.assertRaises(error):
                self.discovery.record_capability(evidence)

    def test_missing_capability_rejects_unbound_or_incomplete_auditor_evidence(self):
        cases = {}
        wrong_root = self.valid_capability("wrong-root", "missing")
        wrong_root.update(tier="auditor", repository_root=str(self.root / "other"))
        cases["wrong repository root"] = wrong_root

        no_paths = self.valid_capability("no-paths", "missing")
        no_paths.update(tier="auditor", paths=[], coverage_checked=[], source_verified=[])
        cases["no source scope"] = no_paths

        incomplete_pages = self.valid_capability("pages", "missing")
        incomplete_pages.update(tier="auditor", pagination_complete=False)
        cases["incomplete pagination"] = incomplete_pages

        no_relationships = self.valid_capability("relations", "missing")
        no_relationships.update(tier="auditor", relationships_checked=[])
        cases["unchecked relationships"] = no_relationships

        bad_coverage = self.valid_capability("coverage-status", "missing")
        bad_coverage.update(tier="auditor")
        bad_coverage["coverage_checked"] = [{
            "path": "src/ads_report.py", "status": "unknown"
        }]
        cases["unclean coverage"] = bad_coverage

        uncovered_path = self.valid_capability("coverage-path", "missing")
        uncovered_path.update(tier="auditor", paths=["src/ads_report.py", "src/api.py"])
        cases["path missing coverage"] = uncovered_path

        unverified_path = self.valid_capability("source-path", "missing")
        unverified_path.update(tier="auditor", paths=["src/ads_report.py", "src/api.py"])
        unverified_path["coverage_checked"].append({
            "path": "src/api.py", "status": "fallback-verified"
        })
        cases["path missing source verification"] = unverified_path

        nonexistent = self.valid_capability("nonexistent", "missing")
        nonexistent.update(
            tier="auditor", paths=["src/not_there.py"],
            coverage_checked=[{"path": "src/not_there.py", "status": "fallback-verified"}],
            source_verified=["src/not_there.py"],
            source_hashes={"src/not_there.py": "0" * 64},
        )
        cases["nonexistent source path"] = nonexistent

        wrong_hash = self.valid_capability("wrong-hash", "missing")
        wrong_hash.update(tier="auditor", source_hashes={"src/ads_report.py": "0" * 64})
        cases["source hash mismatch"] = wrong_hash

        bad_digest = self.valid_capability("bad-digest", "missing")
        bad_digest.update(tier="auditor", mcp_output_digest="not-a-sha256")
        cases["invalid MCP output digest"] = bad_digest

        for name, evidence in cases.items():
            with self.subTest(case=name), self.assertRaises(MODULE.DiscoveryError):
                self.discovery.record_capability(evidence)

    def test_verified_blocking_data_requires_permissions_and_validation_evidence(self):
        for field, invalid in (
            ("permission_status", "unknown"),
            ("permission_status", "denied"),
            ("validation_method", ""),
            ("validation_evidence", []),
        ):
            evidence = data_source(f"D-{field}-{invalid or 'empty'}")
            evidence[field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(MODULE.DiscoveryError):
                self.discovery.record_data(evidence)

        allowed = data_source("D-not-required")
        allowed["permission_status"] = "not-required"
        allowed["validation_evidence"] = [allowed["validation_evidence"][0]]
        result = self.discovery.record_data(allowed)
        self.assertEqual(result["data_sources"][0]["permission_status"], "not-required")

    def test_data_validation_evidence_rejects_unstructured_or_unverifiable_claims(self):
        cases = {}

        free_text = data_source("D-free-text")
        free_text["validation_evidence"] = ["trust me"]
        cases["free text evidence"] = free_text

        placeholder_method = data_source("D-none-method")
        placeholder_method["validation_method"] = "none"
        cases["placeholder method"] = placeholder_method

        missing_permission = data_source("D-no-proof")
        missing_permission["validation_evidence"] = [missing_permission["validation_evidence"][0]]
        cases["verified permission without proof"] = missing_permission

        no_sample = data_source("D-no-sample")
        no_sample["validation_evidence"] = [no_sample["validation_evidence"][1]]
        cases["blocking verified data without sample or experiment"] = no_sample

        bad_digest = data_source("D-bad-digest")
        bad_digest["validation_evidence"][0]["digest"] = "not-a-sha256"
        cases["invalid evidence digest"] = bad_digest

        bad_kind = data_source("D-bad-kind")
        bad_kind["validation_evidence"][0]["kind"] = "testimonial"
        cases["unsupported evidence kind"] = bad_kind

        empty_reference = data_source("D-empty-reference")
        empty_reference["validation_evidence"][0]["reference"] = ""
        cases["empty reference"] = empty_reference

        empty_result = data_source("D-empty-result")
        empty_result["validation_evidence"][0]["result"] = ""
        cases["empty result"] = empty_result

        for name, evidence in cases.items():
            with self.subTest(case=name), self.assertRaises(MODULE.DiscoveryError):
                self.discovery.record_data(evidence)

    def test_unknown_and_unavailable_blocking_data_can_be_recorded_but_not_approved(self):
        for status in ("unknown", "unavailable"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                discovery = MODULE.Discovery(Path(folder))
                discovery.init("data gate", "data gate")
                self.answer_core_questions(discovery)
                discovery.record_capability(capability(repository_root=discovery.root))
                recorded = discovery.record_data(data_source("D1", status, blocking=True))
                self.assertEqual(recorded["data_sources"][0]["status"], status)
                discovery.add_acceptance({
                    "id": "AC1", "criterion": "输出建议", "verification": "测试"
                })
                for phase in PHASES[1:-1]:
                    discovery.advance(phase)
                with self.assertRaises(MODULE.DiscoveryError):
                    discovery.approve(self.confirmation(discovery))

    def test_approval_rejects_each_unresolved_gate(self):
        scenarios = {
            "open question": lambda d: d.ask([question()]),
            "conflict": lambda d: d.add_conflict({
                "id": "X1", "summary": "范围冲突", "impact": "无法冻结范围"
            }),
            "high-risk assumption": lambda d: d.add_assumption({
                "id": "A1", "summary": "拥有 Ads API 权限", "impact": "可能无法读取",
                "risk": "high"
            }),
            "unvalidated data": lambda d: d.record_data({
                **data_source("D1", "unknown"),
            }),
            "unknown capability": lambda d: d.record_capability(
                capability("C1", "unknown", d.root)
            ),
        }
        for name, arrange in scenarios.items():
            with self.subTest(gate=name), tempfile.TemporaryDirectory() as folder:
                discovery = MODULE.Discovery(Path(folder))
                discovery.init("gate", "gate")
                discovery.record_capability(capability(repository_root=discovery.root))
                discovery.record_data(data_source())
                discovery.add_acceptance({
                    "id": "AC-base", "criterion": "输出可审核建议", "verification": "测试"
                })
                self.answer_core_questions(discovery)
                arrange(discovery)
                for phase in PHASES[1:-1]:
                    discovery.advance(phase)
                with self.assertRaises(MODULE.DiscoveryError):
                    discovery.approve(self.confirmation(discovery))

    def test_resolved_requirements_can_be_approved_and_emit_handoff_seed(self):
        self.discovery.ask([question()])
        self.discovery.answer("Q1", choice_id="recommended", disposition="recommended")
        self.answer_core_questions(self.discovery)
        self.discovery.add_conflict({
            "id": "X1", "summary": "自动化级别冲突", "impact": "写操作风险"
        })
        self.discovery.resolve_conflict("X1", "第一版仅生成建议")
        self.discovery.add_assumption({
            "id": "A1", "summary": "测试账号可读取报表", "impact": "数据不可用",
            "risk": "high",
        })
        self.discovery.resolve_assumption(
            "A1", "已用脱敏测试账号验证"
        )
        self.discovery.record_data(data_source("D1"))
        self.discovery.record_capability(self.valid_capability("C1"))
        self.discovery.add_acceptance({
            "id": "AC1", "criterion": "输入报表后生成可审核的建议",
            "verification": "integration test",
        })
        self.advance_to_review()

        confirmation = self.confirmation(self.discovery)
        approved = self.discovery.approve(confirmation)
        self.assertEqual(approved["phase"], "APPROVED")
        self.assertEqual(
            approved["approval_reference"], confirmation
        )
        seed = self.discovery.handoff_seed()
        self.assertTrue(seed["goal"])
        self.assertTrue(seed["acceptance"])
        self.assertEqual(seed["requirements_document"], "docs/PROJECT.md")
        self.assertIn("discovery_revision", seed)
        self.assertIn("reuse", seed)
        self.assertIn("missing", seed)

    def test_each_mutation_refreshes_same_document_sections(self):
        document_path = self.root / "docs/PROJECT.md"
        original = document_path.read_text(encoding="utf-8")
        self.discovery.advance("DISCOVERY")
        self.discovery.ask([question()])
        self.discovery.add_conflict({
            "id": "X1", "summary": "业务规则冲突", "impact": "无法确定输出"
        })
        self.discovery.add_assumption({
            "id": "A1", "summary": "可以取得成本数据", "impact": "利润不准确",
            "risk": "high"
        })
        updated = document_path.read_text(encoding="utf-8")

        self.assertNotEqual(original, updated)
        for expected in (
            "DISCOVERY", "等待用户", "第一版采用哪种实现", "风险", "业务规则冲突",
            "可以取得成本数据", "决策",
        ):
            self.assertIn(expected, updated)
        self.assertEqual(list(self.root.rglob("*.md")), [document_path])

        persisted = json.loads(
            (self.root / ".workflow/amazon-requirements.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["phase"], "DISCOVERY")

    def test_frontend_uses_live_review_gate_not_screenshot_evidence(self):
        def frontend(status, local_url="", evidence="pending", confirmation=None):
            return {
                "id": "UI1", "page": "广告建议审核页", "status": status,
                "local_url": local_url, "visual_evidence": evidence,
                "user_confirmation": confirmation,
            }

        for status in ("planned", "implemented", "technical-verified"):
            self.discovery.add_frontend(frontend(status))
        result = self.discovery.add_frontend(frontend(
            "awaiting-user-live-review", "http://localhost:3000/ads/review",
            "live-local-page",
        ))
        status = self.discovery.status()
        gates = status["frontends"]
        self.assertTrue(gates)
        serialized = json.dumps(gates, ensure_ascii=False)
        self.assertIn("awaiting-user-live-review", serialized)
        self.assertNotIn("screenshot", serialized.lower())
        self.assertNotIn("截图", serialized)
        self.assertEqual(result["frontends"][0]["status"], "awaiting-user-live-review")

        document = (self.root / "docs/PROJECT.md").read_text(encoding="utf-8")
        self.assertIn("awaiting-user-live-review", document)
        self.assertIn("本地", document)
        self.assertNotIn("截图作为默认", document)

        with self.assertRaises(MODULE.DiscoveryError):
            self.discovery.add_frontend(frontend(
                "done", "http://localhost:3000/ads/review", "live-local-page"
            ))
        with self.assertRaises(MODULE.DiscoveryError):
            self.discovery.add_frontend(frontend(
                "user-approved", "https://example.com/review", "live-local-page"
            ))
        with self.assertRaises(MODULE.DiscoveryError):
            self.discovery.add_frontend(frontend(
                "user-approved", "http://localhost:3000/ads/review", "screenshot"
            ))

        self.discovery.add_frontend(frontend(
            "revision-requested", "http://localhost:3000/ads/review", "live-local-page"
        ))
        self.discovery.add_frontend(frontend("implemented"))
        self.discovery.add_frontend(frontend("technical-verified"))
        self.discovery.add_frontend(frontend(
            "awaiting-user-live-review", "http://127.0.0.1:3000/ads/review",
            "live-local-page",
        ))
        local_url = "http://127.0.0.1:3000/ads/review"
        frontend_confirmation = {
            "source": "user-message", "reference": "user-approved-ui-v2",
            "baseline_revision": self.discovery.status()["revision"],
            "local_url": local_url, "confirmed": True,
        }
        self.discovery.add_frontend(frontend(
            "user-approved", local_url, "live-local-page", frontend_confirmation
        ))
        final = self.discovery.add_frontend(frontend(
            "done", local_url, "live-local-page", frontend_confirmation
        ))
        self.assertEqual(len(final["frontends"]), 1)
        self.assertEqual(final["frontends"][0]["status"], "done")

    def test_frontend_rejects_nonplanned_initial_state_and_deceptive_local_urls(self):
        def item(status, url="", confirmation=None):
            return {
                "id": "UI1", "page": "审核页", "status": status,
                "local_url": url, "visual_evidence": "live-local-page",
                "user_confirmation": confirmation,
            }

        with self.assertRaises(MODULE.DiscoveryError):
            self.discovery.add_frontend(item("implemented"))

        for url in (
            "http://localhost.evil.example:3000/review",
            "https://localhost:3000/review",
        ):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as folder:
                discovery = MODULE.Discovery(Path(folder))
                discovery.init("frontend", "frontend")
                for status in ("planned", "implemented", "technical-verified"):
                    discovery.add_frontend({
                        **item(status), "visual_evidence": "pending"
                    })
                with self.assertRaises(MODULE.DiscoveryError):
                    discovery.add_frontend(item("awaiting-user-live-review", url))

    def test_frontend_user_approval_confirmation_is_bound_to_revision_and_url(self):
        url = "http://localhost:3000/review"
        base = {
            "id": "UI1", "page": "审核页", "local_url": "",
            "visual_evidence": "pending", "user_confirmation": None,
        }
        for status in ("planned", "implemented", "technical-verified"):
            self.discovery.add_frontend({**base, "status": status})
        self.discovery.add_frontend({
            **base, "status": "awaiting-user-live-review", "local_url": url,
            "visual_evidence": "live-local-page",
        })
        revision = self.discovery.status()["revision"]
        valid = {
            "source": "user-message", "reference": "user-ui-confirmation",
            "baseline_revision": revision, "local_url": url, "confirmed": True,
        }
        for invalid in (
            None,
            {**valid, "baseline_revision": revision - 1},
            {**valid, "local_url": "http://localhost:3000/other"},
            {**valid, "source": "agent-inference"},
            {**valid, "confirmed": False},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(MODULE.DiscoveryError):
                self.discovery.add_frontend({
                    **base, "status": "user-approved", "local_url": url,
                    "visual_evidence": "live-local-page", "user_confirmation": invalid,
                })

        approved = self.discovery.add_frontend({
            **base, "status": "user-approved", "local_url": url,
            "visual_evidence": "live-local-page", "user_confirmation": valid,
        })
        self.assertEqual(approved["frontends"][0]["user_confirmation"], valid)

    def test_approval_requires_explicit_user_confirmation_in_api_and_cli(self):
        self.discovery.record_capability(self.valid_capability())
        self.discovery.record_data(data_source())
        self.discovery.add_acceptance({
            "id": "AC1", "criterion": "产生审核建议", "verification": "集成测试"
        })
        self.answer_core_questions(self.discovery)
        self.advance_to_review()
        with self.assertRaises(TypeError):
            self.discovery.approve()
        with self.assertRaises(MODULE.DiscoveryError):
            self.discovery.approve("user-confirmed-requirements-v1")

        confirmation = self.confirmation(self.discovery)
        for invalid in (
            {**confirmation, "confirmed": False},
            {**confirmation, "baseline_revision": confirmation["baseline_revision"] - 1},
            {**confirmation, "source": "agent-inference"},
            {**confirmation, "reference": ""},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(MODULE.DiscoveryError):
                self.discovery.approve(invalid)

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "approve"],
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirmation", result.stderr)

    def test_unresolved_decision_dispositions_block_approval(self):
        for disposition in ("assumed", "unknown", "deferred"):
            with self.subTest(disposition=disposition), tempfile.TemporaryDirectory() as folder:
                discovery = MODULE.Discovery(Path(folder))
                discovery.init("decision gate", "decision gate")
                discovery.ask([question()])
                discovery.answer("Q1", choice_id="recommended", disposition=disposition)
                for index, category in enumerate(CORE_CATEGORIES):
                    if category == "scope":
                        continue
                    item = question(f"CORE-{index}", category)
                    discovery.ask([item])
                    discovery.answer(item["id"], choice_id="recommended", disposition="confirmed")
                discovery.record_capability(capability(repository_root=discovery.root))
                discovery.record_data(data_source())
                discovery.add_acceptance({
                    "id": "AC1", "criterion": "输出建议", "verification": "测试"
                })
                for phase in PHASES[1:-1]:
                    discovery.advance(phase)
                with self.assertRaises(MODULE.DiscoveryError):
                    discovery.approve(self.confirmation(discovery))

    def test_approval_requires_every_core_question_category(self):
        for missing in CORE_CATEGORIES:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                discovery = MODULE.Discovery(Path(folder))
                discovery.init("categories", "categories")
                questions = [
                    question(f"Q-{index}", category)
                    for index, category in enumerate(CORE_CATEGORIES) if category != missing
                ]
                discovery.ask(questions[:3])
                if questions[3:]:
                    discovery.ask(questions[3:])
                for item in questions:
                    discovery.answer(item["id"], choice_id="recommended", disposition="confirmed")
                discovery.record_capability(capability(repository_root=discovery.root))
                discovery.record_data(data_source())
                discovery.add_acceptance({
                    "id": "AC1", "criterion": "输出建议", "verification": "测试"
                })
                for phase in PHASES[1:-1]:
                    discovery.advance(phase)
                with self.assertRaises(MODULE.DiscoveryError):
                    discovery.approve(self.confirmation(discovery))

    def test_recommended_disposition_requires_recommended_option(self):
        self.discovery.ask([question()])
        with self.assertRaises(MODULE.DiscoveryError):
            self.discovery.answer("Q1", choice_id="custom", disposition="recommended")
        self.assertEqual(self.discovery.status()["questions"][0]["status"], "open")
        result = self.discovery.answer(
            "Q1", choice_id="recommended", disposition="recommended"
        )
        self.assertEqual(result["decisions"][0]["disposition"], "recommended")

    def test_capability_generation_rejects_placeholder_values(self):
        for index, generation in enumerate(("unknown", "none", "null", "UNKNOWN")):
            evidence = self.valid_capability(f"C{index}")
            evidence["generation"] = generation
            with self.subTest(generation=generation), self.assertRaises(MODULE.DiscoveryError):
                self.discovery.record_capability(evidence)

    def test_open_question_options_are_rendered_in_project_document(self):
        self.discovery.ask([question()])
        document = (self.root / "docs/PROJECT.md").read_text(encoding="utf-8")
        for expected in (
            "复用现有客户端", "优势：", "交付快", "劣势：",
            "受现有接口约束", "权衡：", "用较小改动换取较少定制能力",
            "推荐理由：", "符合最小修改原则", "（推荐）",
            "成本/工作量：", "风险：", "可复用：", "新增工作：",
        ):
            self.assertIn(expected, document)
        self.assertEqual(
            list(self.root.rglob("*.md")), [self.root / "docs/PROJECT.md"]
        )


if __name__ == "__main__":
    unittest.main()
