# Project baseline schema

Maintain one `docs/PROJECT.md` per project. It is the user-facing view; the workflow ledger may remain the machine source of truth. Update dynamic sections on every meaningful state transition and before checkpoint or handoff.

## Required sections

1. **Project status** — lifecycle stage, current state, last updated time, and next gate.
2. **Business objective** — target user, current workflow/problem, desired outcome, measurable success, marketplace, store/account scope, and currency.
3. **Scope** — confirmed in-scope, out-of-scope, deferred items, constraints, and non-goals.
4. **Data and permissions** — each source records `permission_status`, `validation_method`, and `validation_evidence`, plus freshness, ownership, API scopes, write authority, retention, privacy, and compliance. `validation_evidence` is a structured list whose items are `{kind, reference, digest, result}`; `kind` is exactly `sample`, `experiment`, or `permission-proof`. A blocking source may be `verified` only with `sample` or `experiment` evidence. `permission_status=verified` additionally requires `permission-proof`. Root verifies each reference, digest, and result against the real API response, file, or tool record. Scripts may validate only the structure and digest format; they cannot authenticate the evidence. “Trust me” assertions and unstructured free text do not constitute evidence.
5. **Existing capability analysis** — graph project/generation, evidence scope and coverage limitations, capability status, reusable paths/symbols, and confirmed gaps. Evidence also binds `mcp_output_digest` to Root's actual MCP response summary and `source_hashes` to real repository file contents.
6. **Questions, options, and decisions** — each question records `category` plus response modes `accept-recommendation`, `request-recommendation`, `defer`, and `custom`. Each business option records `advantages`, `disadvantages`, `cost_effort`, `risks`, `reusable_capabilities`, `new_work`, `tradeoffs`, and `recommendation_reason`. The advantages, disadvantages, risks, reusable-capabilities, new-work, and tradeoffs lists each require at least one item; use `待验证` for unknowns, never an empty list. Keep system recommendations, accepted recommendations, user decisions, and deferrals distinct.
7. **Business rules and user flow** — inputs, outputs, edge cases, approvals, limits, audit, failure behavior, and rollback.
8. **Acceptance criteria** — observable and testable criteria, including required evidence and frontend live-review status.
9. **Technical approach and delivery plan** — only after sufficient discovery; identify configuration, reuse, composition, adapters, extensions, and genuinely new modules.
10. **Implementation progress** — features/tasks by state, current work, completed work, technical verification, user approval, and next steps. Show percentages only when explicit effort weights exist.
11. **Blockers and user actions** — cause, impact, owner, unblock condition, and decisions or live reviews awaiting the user.
12. **Assumptions, risks, and change log** — distinguish unresolved assumptions from accepted risks; append material requirement and decision changes without erasing history.

## Approval gate

Set `APPROVED` only after the user explicitly confirms the current baseline revision and all are true:

- critical business outcomes and measurable criteria are confirmed;
- data sources and permissions are validated or bounded by an accepted experiment;
- existing APIs/modules and coverage limitations are documented;
- reuse, adaptation, extension, and new work are distinguished;
- important tradeoffs have been shown and key choices made;
- high-risk assumptions are resolved;
- acceptance criteria are testable.
- the five core decision categories `business-goal`, `success-metric`, `user-workflow`, `scope`, and `automation-authority` are complete;
- Root has verified a structured reference to the real approving user message and bound it to the exact baseline revision.

An audit script can reject missing or malformed evidence, but it cannot certify that a message is authentic. Root must not synthesize, infer, or rewrite user approval. Before valid approval, record the next required user decision and do not produce execution tasks.

## Evidence contracts

A `missing` API/module decision records:

- `repository_root`;
- graph `project` and `generation`;
- bounded `scope`;
- `pagination_complete`;
- `relationships_checked`;
- structured coverage status, gaps, ranges, and reasons;
- `mcp_output_digest`, derived from Root's actual MCP response summary and checked by Root against the real tool record;
- source readback for candidates and every relevant uncovered range.
- `source_hashes`, calculated from the current contents of every cited repository source file.

A script may validate the evidence shape, resolve paths, and compare source hashes, but cannot authenticate the origin of an MCP response. Nonexistent paths and error summaries fail immediately. Incomplete negative evidence yields `unknown`, not `missing`.

Frontend state begins at `planned` and advances through `implemented`, `technical-verified`, `awaiting-user-live-review`, then `revision-requested` or `user-approved`, and finally `done`. Parse the reviewed URL; its host must be exactly `localhost` or `127.0.0.1`. Screenshots are invalid approval evidence. User approval records a Root-verified structured reference to the real user message, current baseline revision, and reviewed local URL. Root must not manufacture that evidence.

Other useful project states include `clarifying`, `awaiting-user-decision`, `approved`, `running`, `blocked`, and `failed`.
