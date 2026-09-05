---
name: amazon-requirements-discovery
description: Clarify vague Amazon cross-border e-commerce software, automation, or operations-tool requests into an approved, testable project baseline before implementation. Use when the user has an Amazon business idea but goals, workflow, data, permissions, scope, or acceptance criteria remain uncertain; do not use for a narrow, already-approved implementation task.
---

# Amazon Requirements Discovery

Turn an ambiguous Amazon operations idea into a user-approved, implementable requirement baseline. Discovery is a gate: while status is not `APPROVED`, do not create execution tasks, write production code, or dispatch implementation agents.

## Operating rules

- State what is known, inferred, unknown, and conflicting. Never present an assumption or recommendation as a user decision.
- Ask 1–3 high-value questions per round, prioritizing business impact, uncertainty, error cost, and ease of answering. Every question has a `category` and all four `response_modes`: `accept-recommendation`, `request-recommendation`, `defer`, and `custom`.
- Every business option supplies `advantages`, `disadvantages`, `cost_effort`, `risks`, `reusable_capabilities`, `new_work`, `tradeoffs`, and `recommendation_reason`. The advantages, disadvantages, risks, reusable-capabilities, new-work, and tradeoffs lists each contain at least one item; write `待验证` for an unknown rather than leaving a list empty. Do not offer an incomplete option.
- Prefer the smallest change that satisfies confirmed needs. Reuse existing code and infrastructure; validate only at system boundaries; fail visibly rather than masking errors with broad fallbacks.
- Keep one user-facing source of truth at `docs/PROJECT.md`. Update it whenever requirements, decisions, implementation progress, blockers, evidence, or user actions change. Do not create separate status documents.

Read [references/project-schema.md](references/project-schema.md) when creating or updating the project baseline. Read [references/amazon-domains.md](references/amazon-domains.md) only for the relevant Amazon domain(s).

## Discovery flow

1. Establish the business outcome, user, current workflow, measurable success, scope, and constraints. Record unresolved items instead of guessing.
2. Inspect the current repository through the codebase knowledge graph before proposing implementation. Confirm the graph project/generation, use task-directed evidence, retrieve material source snippets, and check coverage for candidate paths.
3. Classify relevant capabilities as `implemented`, `reusable`, `partial`, `adapter-required`, `missing`, `unknown`, or `deprecated`. Evaluate implementation approaches in this order:
   `configuration → reuse API → compose services → minimal adapter → extend module → new module`.
4. Treat `missing` as a negative claim. Its evidence must bind `repository_root`, graph `project` and `generation`, bounded `scope`, `pagination_complete`, `relationships_checked`, structured coverage results, `mcp_output_digest` derived from Root's actual MCP response, source readback for every material candidate or uncovered range, and `source_hashes` calculated from real repository file contents. Root verifies the digest against the actual tool record; a script can validate fields and files but cannot authenticate the MCP response's origin. A nonexistent path or error summary fails immediately. If the bounded Auditor cannot supply all evidence, use `unknown` rather than `missing`.
5. Verify data availability and quality, API/account permissions, marketplace and currency, automation authority, approvals, audit/rollback needs, compliance exposure, time, and budget. Every data source records `permission_status`, `validation_method`, and `validation_evidence`, where `validation_evidence` is a structured list of `{kind, reference, digest, result}` and `kind` is exactly `sample`, `experiment`, or `permission-proof`. A blocking source becomes `verified` only with `sample` or `experiment` evidence; `permission_status=verified` requires `permission-proof`. Root checks every item against the real API response, file, or tool record. A script may validate structure and digest format but cannot authenticate the underlying evidence. “Trust me” claims and unstructured free text are not evidence. Use a minimal validation experiment before committing to an expensive or uncertain feature.
6. Present feasible alternatives and tradeoffs, then record the user's selection separately from system recommendations and accepted recommendations.
7. Iterate until critical objectives, data, permissions, reuse/gaps, high-risk assumptions, and testable acceptance criteria are resolved. Before freezing, all five core decision categories must be complete: `business-goal`, `success-metric`, `user-workflow`, `scope`, and `automation-authority`.
8. Ask the user to approve the current baseline revision explicitly. Approval is valid only when Root has verified a structured reference to a real user message and bound it to that exact baseline revision. A script may enforce audit fields but cannot authenticate message truth; Root must never create, infer, or rewrite the approving user message. Only then set status to `APPROVED` and allow task planning.

Recommended lifecycle:

`IDEA → DISCOVERY → DATA_VALIDATION → SOLUTION_OPTIONS → PROTOTYPE_VALIDATION → REQUIREMENTS_REVIEW → APPROVED → TASK_PLANNING → EXECUTION`

Advance one recorded phase at a time so the history stays auditable. An inapplicable phase may be completed with a recorded reason, but must not be silently skipped. Never skip explicit user approval.

## Implementation and acceptance continuity

After approval, keep `docs/PROJECT.md` current from the workflow ledger rather than relying on conversational claims. Distinguish modified, executed, technically verified, and user-approved states.

Frontend work starts at `planned` and advances only through recorded transitions: `planned → implemented → technical-verified → awaiting-user-live-review → revision-requested | user-approved → done`. After technical verification, start the local application and open its URL for direct user inspection. Parse the URL and require its host to equal `localhost` or `127.0.0.1`; substring matches and other hosts are invalid. Screenshots are never approval evidence. `user-approved` requires Root-verified structured reference to a real user message bound to the reviewed baseline revision and local URL; Root must not manufacture it.
