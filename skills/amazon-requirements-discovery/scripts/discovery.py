"""Persistent Amazon requirements discovery with one generated project document."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse


PHASES = (
    "IDEA", "DISCOVERY", "DATA_VALIDATION", "SOLUTION_OPTIONS",
    "PROTOTYPE_VALIDATION", "REQUIREMENTS_REVIEW", "APPROVED",
)
DISPOSITIONS = {"confirmed", "recommended", "assumed", "unknown", "rejected", "deferred"}
CAPABILITY_STATES = {
    "implemented", "reusable", "partial", "adapter-required",
    "missing", "unknown", "deprecated",
}
FRONTEND_STATES = {
    "planned", "implemented", "technical-verified", "awaiting-user-live-review",
    "revision-requested", "user-approved", "done",
}
CORE_DECISION_CATEGORIES = {
    "business-goal", "success-metric", "user-workflow", "scope", "automation-authority",
}
RESPONSE_MODES = {
    "accept-recommendation", "request-recommendation", "defer", "custom",
}


class DiscoveryError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DiscoveryError(message)


def text(value, name):
    require(isinstance(value, str) and value.strip(), f"{name} required")
    return value.strip()


def string_list(value, name):
    require(isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
            f"{name} must be a string list")
    return [v.strip() for v in value]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class Discovery:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.state_path = self.root / ".workflow" / "amazon-requirements.json"
        self.document_path = self.root / "docs" / "PROJECT.md"

    def init(self, title, brief):
        require(not self.state_path.exists(), "Amazon requirements discovery already initialized")
        state = {
            "version": 1, "revision": 0, "title": text(title, "title"),
            "brief": text(brief, "brief"), "phase": "IDEA", "questions": [],
            "decisions": [], "capabilities": [], "data_sources": [],
            "conflicts": [], "assumptions": [], "acceptance": [], "frontends": [],
            "events": [],
        }
        self._event(state, "initialized")
        self._save(state)
        return state

    def status(self):
        require(self.state_path.is_file(), "Amazon requirements discovery not initialized")
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def ask(self, questions):
        require(isinstance(questions, list) and 1 <= len(questions) <= 3,
                "ask one to three related questions per round")
        state = self.status()
        existing = {item["id"] for item in state["questions"]}
        for question in questions:
            required = {"id", "category", "prompt", "why_now", "response_modes", "options"}
            require(isinstance(question, dict) and set(question) == required,
                    "question fields must be id, prompt, why_now, options")
            qid = text(question["id"], "question.id")
            require(qid not in existing, "duplicate question id")
            category = text(question["category"], "question.category")
            response_modes = string_list(question["response_modes"], "question.response_modes")
            require(len(response_modes) == len(RESPONSE_MODES) and set(response_modes) == RESPONSE_MODES,
                    "question must support accept recommendation, request recommendation, defer, and custom")
            options = question["options"]
            require(isinstance(options, list) and 2 <= len(options) <= 4,
                    "question must provide two to four options")
            option_ids = set()
            normalized = []
            fields = {"id", "label", "advantages", "disadvantages", "cost_effort", "risks",
                      "reusable_capabilities", "new_work", "tradeoffs", "recommendation_reason",
                      "recommended"}
            for option in options:
                require(isinstance(option, dict) and set(option) == fields,
                        "option fields do not match the decision schema")
                oid = text(option["id"], "option.id")
                require(oid not in option_ids, "duplicate option id")
                require(type(option["recommended"]) is bool, "option.recommended must be boolean")
                lists = {}
                for key in ("advantages", "disadvantages", "risks", "reusable_capabilities",
                            "new_work", "tradeoffs"):
                    lists[key] = string_list(option[key], f"option.{key}")
                    require(lists[key], f"option.{key} must contain at least one item")
                normalized.append({"id": oid, "label": text(option["label"], "option.label"),
                    "advantages": lists["advantages"],
                    "disadvantages": lists["disadvantages"],
                    "cost_effort": text(option["cost_effort"], "option.cost_effort"),
                    "risks": lists["risks"],
                    "reusable_capabilities": lists["reusable_capabilities"],
                    "new_work": lists["new_work"],
                    "tradeoffs": lists["tradeoffs"],
                    "recommendation_reason": text(option["recommendation_reason"], "recommendation_reason"),
                    "recommended": option["recommended"]})
                option_ids.add(oid)
            state["questions"].append({"id": qid, "category": category,
                "prompt": text(question["prompt"], "prompt"),
                "why_now": text(question["why_now"], "why_now"), "options": normalized,
                "response_modes": sorted(RESPONSE_MODES),
                "status": "open", "answer": None})
            existing.add(qid)
        self._event(state, "questions-added", count=len(questions))
        self._save(state)
        return state

    def answer(self, question_id, choice_id=None, custom=None, disposition="confirmed"):
        require(disposition in DISPOSITIONS, "unsupported decision disposition")
        require(bool(choice_id) != bool(custom), "provide exactly one option choice or custom answer")
        require(disposition != "recommended" or bool(choice_id),
                "recommended disposition requires accepting a recommended option")
        state = self.status()
        question = next((item for item in state["questions"] if item["id"] == question_id), None)
        require(question is not None and question["status"] == "open", "open question not found")
        if choice_id:
            option = next((item for item in question["options"] if item["id"] == choice_id), None)
            require(option is not None, "unknown option")
            require(disposition != "recommended" or option["recommended"],
                    "recommended disposition requires accepting the recommended option")
            answer = {"choice_id": choice_id, "value": option["label"]}
        else:
            answer = {"choice_id": None, "value": text(custom, "custom answer")}
        question.update(status="answered", answer=answer)
        state["decisions"].append({"question_id": question_id, "category": question["category"],
                                   "value": answer["value"],
                                   "disposition": disposition})
        self._event(state, "question-answered", question_id=question_id,
                    disposition=disposition)
        self._save(state)
        return state

    def record_capability(self, evidence):
        required = {"id", "name", "status", "reuse", "symbols", "paths", "repository_root",
                    "graph_project", "generation", "tier", "scope", "pagination_complete",
                    "relationships_checked", "coverage_checked", "source_verified",
                    "mcp_output_digest", "source_hashes"}
        require(isinstance(evidence, dict) and set(evidence) == required,
                "capability evidence fields do not match the schema")
        status = evidence["status"]
        require(status in CAPABILITY_STATES, "unsupported capability status")
        tier = evidence["tier"]
        require(tier in ("scout", "verify", "auditor"), "unsupported graph evidence tier")
        require(status != "missing" or tier == "auditor", "missing capability requires bounded Auditor evidence")
        for key in ("id", "name", "reuse", "repository_root", "graph_project", "generation", "scope"):
            text(evidence[key], f"capability.{key}")
        require(Path(evidence["repository_root"]).resolve() == self.root,
                "capability evidence repository root mismatch")
        require(type(evidence["pagination_complete"]) is bool,
                "capability.pagination_complete must be boolean")
        require(isinstance(evidence["mcp_output_digest"], str) and
                re.fullmatch(r"[0-9a-fA-F]{64}", evidence["mcp_output_digest"]),
                "capability evidence must bind a SHA-256 MCP output digest")
        normalized = dict(evidence)
        for key in ("symbols", "paths", "relationships_checked", "source_verified"):
            normalized[key] = string_list(evidence[key], f"capability.{key}")
        require(normalized["paths"], "capability evidence requires bounded source paths")
        require(isinstance(evidence["source_hashes"], dict) and
                set(evidence["source_hashes"]) == set(normalized["paths"]),
                "source_hashes must cover every capability evidence path")
        normalized["source_hashes"] = {}
        for relative_path in normalized["paths"]:
            candidate = (self.root / relative_path).resolve()
            require(candidate.is_relative_to(self.root) and candidate.is_file(),
                    "capability evidence path must be an existing repository file")
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            require(evidence["source_hashes"].get(relative_path) == actual,
                    "capability source hash does not match repository content")
            normalized["source_hashes"][relative_path] = actual
        coverage = evidence["coverage_checked"]
        require(isinstance(coverage, list) and coverage, "capability coverage evidence required")
        normalized["coverage_checked"] = []
        for item in coverage:
            require(isinstance(item, dict) and set(item) == {"path", "status"},
                    "coverage item fields must be path and status")
            path = text(item["path"], "coverage.path")
            require(item["status"] in ("no-recorded-gap", "fallback-verified"),
                    "coverage status is not verified")
            normalized["coverage_checked"].append({"path": path, "status": item["status"]})
        covered = {item["path"] for item in normalized["coverage_checked"]}
        require(set(normalized["paths"]) <= covered and
                set(normalized["paths"]) <= set(normalized["source_verified"]),
                "every capability evidence path requires coverage and source verification")
        require(evidence["generation"].casefold() not in ("unknown", "none", "null"),
                "capability generation must identify a real index generation")
        require(status != "missing" or (evidence["pagination_complete"] and
                normalized["relationships_checked"]),
                "missing capability requires complete pagination and relationship checks")
        state = self.status()
        require(evidence["id"] not in {item["id"] for item in state["capabilities"]},
                "duplicate capability id")
        state["capabilities"].append(normalized)
        self._event(state, "capability-recorded", capability=evidence["id"], status=status)
        self._save(state)
        return state

    def record_data(self, evidence):
        required = {"id", "name", "status", "source", "boundary", "blocking",
                    "permission_status", "validation_method", "validation_evidence"}
        require(isinstance(evidence, dict) and set(evidence) == required,
                "data evidence fields do not match the schema")
        require(evidence["status"] in ("verified", "unavailable", "unknown"),
                "unsupported data status")
        require(type(evidence["blocking"]) is bool, "data.blocking must be boolean")
        require(evidence["permission_status"] in ("verified", "not-required", "unavailable", "unknown"),
                "unsupported data permission status")
        for key in ("id", "name", "source", "boundary", "validation_method"):
            text(evidence[key], f"data.{key}")
        require(evidence["validation_method"].casefold() not in ("none", "unknown", "n/a", "na"),
                "data validation method must describe an actual check")
        validation_evidence = evidence["validation_evidence"]
        require(isinstance(validation_evidence, list), "data.validation_evidence must be a list")
        normalized_evidence = []
        evidence_kinds = set()
        for item in validation_evidence:
            require(isinstance(item, dict) and set(item) == {"kind", "reference", "digest", "result"},
                    "data validation evidence fields must be kind, reference, digest, and result")
            require(item["kind"] in ("sample", "experiment", "permission-proof"),
                    "unsupported data validation evidence kind")
            text(item["reference"], "data evidence reference")
            text(item["result"], "data evidence result")
            require(isinstance(item["digest"], str) and re.fullmatch(r"[0-9a-fA-F]{64}", item["digest"]),
                    "data validation evidence must bind a SHA-256 digest")
            normalized_evidence.append(dict(item))
            evidence_kinds.add(item["kind"])
        if evidence["status"] == "verified" and evidence["blocking"]:
            require(evidence["permission_status"] in ("verified", "not-required"),
                    "blocking verified data requires verified or unnecessary permission")
            require(evidence_kinds & {"sample", "experiment"},
                    "blocking verified data requires sample or experiment evidence")
            require(evidence["permission_status"] != "verified" or "permission-proof" in evidence_kinds,
                    "verified data permission requires permission proof")
        state = self.status()
        require(evidence["id"] not in {item["id"] for item in state["data_sources"]},
                "duplicate data id")
        state["data_sources"].append({**evidence, "validation_evidence": normalized_evidence})
        self._event(state, "data-recorded", data=evidence["id"], status=evidence["status"])
        self._save(state)
        return state

    def add_conflict(self, item):
        return self._append_item("conflicts", item, {"id", "summary", "impact"}, "conflict-added")

    def resolve_conflict(self, item_id, resolution):
        return self._resolve_item("conflicts", item_id, text(resolution, "resolution"), "conflict-resolved")

    def add_assumption(self, item):
        required = {"id", "summary", "impact", "risk"}
        require(isinstance(item, dict) and set(item) == required, "assumption fields do not match schema")
        require(item["risk"] in ("low", "medium", "high"), "unsupported assumption risk")
        return self._append_item("assumptions", item, required, "assumption-added")

    def resolve_assumption(self, item_id, evidence):
        return self._resolve_item("assumptions", item_id, text(evidence, "evidence"), "assumption-resolved")

    def add_acceptance(self, item):
        required = {"id", "criterion", "verification"}
        require(isinstance(item, dict) and set(item) == required, "acceptance fields do not match schema")
        return self._append_item("acceptance", item, required, "acceptance-added", resolved=True)

    def add_frontend(self, item):
        required = {"id", "page", "status", "local_url", "visual_evidence", "user_confirmation"}
        require(isinstance(item, dict) and set(item) == required, "frontend fields do not match schema")
        require(item["status"] in FRONTEND_STATES, "unsupported frontend status")
        state = self.status()
        if item["status"] in ("awaiting-user-live-review", "user-approved", "done"):
            parsed = urlparse(text(item["local_url"], "frontend.local_url"))
            require(parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"),
                    "frontend user review requires an exact local HTTP page URL")
        require(item["visual_evidence"] != "screenshot", "screenshot cannot replace live local page review")
        if item["status"] in ("user-approved", "done"):
            require(item["visual_evidence"] == "live-local-page", "user approval must come from live local page review")
        current = next((entry for entry in state["frontends"] if entry["id"] == item["id"]), None)
        transitions = {
            "planned": {"implemented"},
            "implemented": {"technical-verified"},
            "technical-verified": {"awaiting-user-live-review"},
            "awaiting-user-live-review": {"revision-requested", "user-approved"},
            "revision-requested": {"implemented"},
            "user-approved": {"done"},
            "done": set(),
        }
        if current:
            require(item["status"] in transitions[current["status"]], "invalid frontend status transition")
            if item["status"] == "user-approved":
                self._validate_user_confirmation(item["user_confirmation"], state["revision"], item["local_url"])
            elif item["status"] == "done":
                require(item["user_confirmation"] == current["user_confirmation"],
                        "done must retain the live-review user confirmation")
            else:
                require(item["user_confirmation"] is None, "user confirmation is only accepted at user-approved")
            current.update(item)
        else:
            require(item["status"] == "planned", "new frontend work must start at planned")
            require(item["user_confirmation"] is None, "planned frontend cannot be user-approved")
            state["frontends"].append(dict(item))
        self._event(state, "frontend-recorded", frontend=item["id"], status=item["status"])
        self._save(state)
        return state

    def advance(self, target):
        require(target in PHASES[:-1], "APPROVED is reached only through approve")
        state = self.status()
        current = PHASES.index(state["phase"])
        require(PHASES.index(target) == current + 1, "advance exactly one discovery phase")
        if target == "SOLUTION_OPTIONS":
            require(state["capabilities"], "repository capability inventory required")
            require(state["data_sources"], "data-source evidence required")
        state["phase"] = target
        self._event(state, "phase-advanced", phase=target)
        self._save(state)
        return state

    def approve(self, user_confirmation):
        state = self.status()
        self._validate_user_confirmation(user_confirmation, state["revision"])
        require(state["phase"] == "REQUIREMENTS_REVIEW", "requirements must reach review before approval")
        blockers = self._approval_blockers(state)
        require(not blockers, "; ".join(blockers))
        state["phase"] = "APPROVED"
        state["approval_reference"] = dict(user_confirmation)
        self._event(state, "requirements-approved")
        self._save(state)
        return state

    def handoff_seed(self):
        state = self.status()
        require(state["phase"] == "APPROVED", "approve requirements before execution handoff")
        return {"goal": state["brief"], "requirements_document": "docs/PROJECT.md",
                "discovery_revision": state["revision"],
                "acceptance": [item["criterion"] for item in state["acceptance"]],
                "reuse": [item for item in state["capabilities"]
                          if item["status"] in ("implemented", "reusable", "partial", "adapter-required")],
                "missing": [item for item in state["capabilities"] if item["status"] == "missing"]}

    def sync_document(self):
        state = self.status()
        self._write_document(state)
        return {"path": "docs/PROJECT.md", "revision": state["revision"], "phase": state["phase"]}

    def _append_item(self, collection, item, required, action, resolved=False):
        require(isinstance(item, dict) and set(item) == required, f"{collection} fields do not match schema")
        for key, value in item.items():
            text(value, f"{collection}.{key}")
        state = self.status()
        require(item["id"] not in {entry["id"] for entry in state[collection]}, f"duplicate {collection} id")
        state[collection].append({**item, "resolved": resolved, "resolution": None})
        self._event(state, action, item=item["id"])
        self._save(state)
        return state

    def _resolve_item(self, collection, item_id, resolution, action):
        state = self.status()
        item = next((entry for entry in state[collection] if entry["id"] == item_id), None)
        require(item is not None and not item["resolved"], f"open {collection} item not found")
        item.update(resolved=True, resolution=resolution)
        self._event(state, action, item=item_id)
        self._save(state)
        return state

    @staticmethod
    def _approval_blockers(state):
        blockers = []
        if any(item["status"] == "open" for item in state["questions"]):
            blockers.append("open questions remain")
        if any(item["disposition"] in ("assumed", "unknown", "deferred") for item in state["decisions"]):
            blockers.append("unresolved assumed, unknown, or deferred decisions remain")
        decided = {item["category"] for item in state["decisions"]
                   if item["disposition"] in ("confirmed", "recommended", "rejected")}
        missing_categories = sorted(CORE_DECISION_CATEGORIES - decided)
        if missing_categories:
            blockers.append("missing core decisions: " + ", ".join(missing_categories))
        if any(not item["resolved"] for item in state["conflicts"]):
            blockers.append("unresolved conflicts remain")
        if any(item["risk"] == "high" and not item["resolved"] for item in state["assumptions"]):
            blockers.append("unresolved high-risk assumptions remain")
        if not state["capabilities"]:
            blockers.append("repository capability inventory missing")
        if any(item["status"] == "unknown" for item in state["capabilities"]):
            blockers.append("unknown repository capabilities remain")
        if not state["data_sources"]:
            blockers.append("data-source evidence missing")
        if any(item["blocking"] and item["status"] != "verified" for item in state["data_sources"]):
            blockers.append("blocking data sources are not verified")
        if not state["acceptance"]:
            blockers.append("acceptance criteria missing")
        return blockers

    @staticmethod
    def _validate_user_confirmation(confirmation, revision, local_url=None):
        fields = {"source", "reference", "baseline_revision", "confirmed"}
        if local_url is not None:
            fields.add("local_url")
        require(isinstance(confirmation, dict) and set(confirmation) == fields,
                "user confirmation fields do not match the audit schema")
        require(confirmation["source"] == "user-message" and confirmation["confirmed"] is True,
                "confirmation must reference an explicit user message")
        text(confirmation["reference"], "user confirmation reference")
        require(confirmation["baseline_revision"] == revision,
                "user confirmation must bind the current baseline revision")
        if local_url is not None:
            require(confirmation["local_url"] == local_url,
                    "frontend confirmation must bind the reviewed local URL")

    @staticmethod
    def _event(state, action, **details):
        state["revision"] += 1
        state["events"].append({"revision": state["revision"], "action": action,
                                "time": datetime.now(timezone.utc).isoformat(), **details})

    def _save(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent,
                                         delete=False, suffix=".tmp") as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        temp_path.replace(self.state_path)
        self._write_document(state)

    def _write_document(self, state):
        self.document_path.parent.mkdir(parents=True, exist_ok=True)
        open_questions = [item for item in state["questions"] if item["status"] == "open"]
        unresolved_conflicts = [item for item in state["conflicts"] if not item["resolved"]]
        unresolved_assumptions = [item for item in state["assumptions"] if not item["resolved"]]
        updated_at = state["events"][-1]["time"] if state["events"] else "unknown"
        lines = [f"# {state['title']}", "", f"> 阶段：`{state['phase']}` · 修订：{state['revision']} · 更新时间：{updated_at}", "",
                 "## 项目目标", "", state["brief"], "", "## 当前实施进度", "",
                 f"- 当前阶段：{state['phase']}",
                 f"- 已确认决策：{len(state['decisions'])}",
                 f"- 已盘点能力：{len(state['capabilities'])}",
                 f"- 开放问题：{len(open_questions)}", "", "## 等待用户处理", ""]
        if open_questions:
            for question in open_questions:
                lines += [f"- **{question['prompt']}**", f"  - 为什么现在确认：{question['why_now']}"]
                for option in question["options"]:
                    marker = "（推荐）" if option["recommended"] else ""
                    lines += [f"  - `{option['id']}` {option['label']}{marker}",
                              f"    - 优势：{'；'.join(option['advantages'])}",
                              f"    - 劣势：{'；'.join(option['disadvantages'])}",
                              f"    - 成本/工作量：{option['cost_effort']}",
                              f"    - 风险：{'；'.join(option['risks'])}",
                              f"    - 可复用：{'；'.join(option['reusable_capabilities'])}",
                              f"    - 新增工作：{'；'.join(option['new_work'])}",
                              f"    - 权衡：{'；'.join(option['tradeoffs'])}",
                              f"    - 推荐理由：{option['recommendation_reason']}"]
        else:
            lines.append("- 无")
        lines += ["", "## 功能范围与非目标", "", "- 随需求澄清持续更新；未确认内容不得进入执行。"]
        lines += ["", "## 已有 API 与复用分析", ""]
        lines.extend([f"- **{item['name']}**：`{item['status']}`；复用策略：{item['reuse']}；"
                      f"图谱：{item['graph_project']}@{item['generation']}；范围：{item['scope']}"
                      for item in state["capabilities"]] or ["- 尚未完成仓库能力盘点"])
        lines += ["", "## 决策记录", ""]
        lines.extend([f"- `{item['disposition']}` {item['question_id']}：{item['value']}"
                      for item in state["decisions"]] or ["- 尚无已确认决策"])
        lines += ["", "## 方案选择与权衡", "",
                  "- 复用顺序：配置 → 复用 API → 组合 Service → 最小 Adapter → 扩展模块 → 新模块。"]
        lines += ["", "## 数据源与权限", ""]
        lines.extend([f"- **{item['name']}**：`{item['status']}`；权限：{item['permission_status']}；"
                      f"验证：{item['validation_method']}；{item['boundary']}"
                      for item in state["data_sources"]] or ["- 尚未验证数据源"])
        lines += ["", "## 风险与阻塞", ""]
        lines.extend([f"- 冲突：{item['summary']}（{item['impact']}）" for item in unresolved_conflicts])
        lines.extend([f"- 假设：{item['summary']}（风险：{item['risk']}）" for item in unresolved_assumptions])
        if not unresolved_conflicts and not unresolved_assumptions:
            lines.append("- 无")
        lines += ["", "## 验收标准", ""]
        lines.extend([f"- {item['criterion']}；验证：{item['verification']}"
                      for item in state["acceptance"]] or ["- 尚未定义"])
        lines += ["", "## 业务规则与用户流程", "", "- 随已确认决策更新。",
                  "", "## 技术方案与交付计划", ""]
        lines.append("- 需求达到 `APPROVED` 后由 Project Workflow 生成执行 DAG。" if state["phase"] == "APPROVED"
                     else "- 尚未批准；禁止拆分执行 Task。")
        lines += ["", "## 前端页面验收", ""]
        lines.extend([f"- **{item['page']}**：`{item['status']}`；本地页面：{item['local_url'] or '待启动'}"
                      for item in state["frontends"]] or ["- 无前端页面需求"])
        lines += ["", "## 最近状态事件", ""]
        lines.extend([f"- r{item['revision']} `{item['action']}`" for item in state["events"][-10:]])
        self.document_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init"); p.add_argument("--title", required=True); p.add_argument("--brief", required=True)
    sub.add_parser("status"); sub.add_parser("sync-document"); sub.add_parser("handoff")
    p = sub.add_parser("approve"); p.add_argument("--confirmation", required=True)
    p = sub.add_parser("ask"); p.add_argument("--questions", required=True)
    p = sub.add_parser("answer"); p.add_argument("--question", required=True); p.add_argument("--choice"); p.add_argument("--custom"); p.add_argument("--disposition", default="confirmed")
    for name in ("capability", "data", "conflict-add", "assumption-add", "acceptance-add", "frontend-add"):
        p = sub.add_parser(name); p.add_argument("--item", required=True)
    for name in ("conflict-resolve", "assumption-resolve"):
        p = sub.add_parser(name); p.add_argument("--id", required=True); p.add_argument("--resolution", required=True)
    p = sub.add_parser("advance"); p.add_argument("--phase", required=True)
    args = parser.parse_args()
    discovery = Discovery(args.root)
    try:
        if args.action == "init": result = discovery.init(args.title, args.brief)
        elif args.action == "status": result = discovery.status()
        elif args.action == "sync-document": result = discovery.sync_document()
        elif args.action == "ask": result = discovery.ask(load_json(args.questions))
        elif args.action == "answer": result = discovery.answer(args.question, args.choice, args.custom, args.disposition)
        elif args.action == "capability": result = discovery.record_capability(load_json(args.item))
        elif args.action == "data": result = discovery.record_data(load_json(args.item))
        elif args.action == "conflict-add": result = discovery.add_conflict(load_json(args.item))
        elif args.action == "conflict-resolve": result = discovery.resolve_conflict(args.id, args.resolution)
        elif args.action == "assumption-add": result = discovery.add_assumption(load_json(args.item))
        elif args.action == "assumption-resolve": result = discovery.resolve_assumption(args.id, args.resolution)
        elif args.action == "acceptance-add": result = discovery.add_acceptance(load_json(args.item))
        elif args.action == "frontend-add": result = discovery.add_frontend(load_json(args.item))
        elif args.action == "advance": result = discovery.advance(args.phase)
        elif args.action == "approve": result = discovery.approve(load_json(args.confirmation))
        else: result = discovery.handoff_seed()
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
        return 0
    except (DiscoveryError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
