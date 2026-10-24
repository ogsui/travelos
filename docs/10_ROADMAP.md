# 10 Roadmap

Order is chosen so the **demo for the owner exists early** (end of Phase 2), and so every outbound or money-touching capability arrives after its safety layer.

| Phase | Tasks | Outcome | Exit gate |
|---|---|---|---|
| 0 Business setup (non-code, start now) | — | Rules interview done, sandbox requests sent, WhatsApp/Meta/payment accounts started, accountant looped in | `config/business_rules.yaml` v1 filled; supplier docs/sandbox received or requested |
| 1 Foundation | 001-007 | Runnable platform, audit, workflows, approvals, LLM gateway, synthetic data | `make check` green; approval + mode tests pass |
| 2 Quote pipeline (DEMO) | 008-018 | Inquiry -> parse -> supplier simulator -> package -> price -> quote PDF -> approval dashboard, with eval + shadow comparator | Replay of 20 past/synthetic inquiries end to end; eval gates in doc 08 met on synthetic gold |
| 3 Transactions | 019-024 | Payments, booking orchestration, billing, documents, support, post-trip | Full inquiry-to-invoice e2e on mocks; failure-injection suite green; accountant reviewed samples |
| 4 Growth engine | 025-032 | CRM intelligence, outreach, content, social, SEO, ads, multi-channel lead capture, website | Each outbound channel behind a kill switch; consent enforcement tests; campaign approval flow tested |
| 5 Intelligence & ops | 033-038 | Analytics, digests, manager/orchestrator, accounting integrations, observability, hardening | Restore drill done; red-team suite green; dashboards match ledger |
| 6 Shadow | 039 | 30 days on real inquiries, nothing sent | Owner go/no-go with metrics |
| 7 Pilot | 040 | Live at A1, narrow scope, then expand | 2 weeks incident-free -> widen scope |
| 8 Graduation (ongoing) | — | Monthly review of autonomy metrics; promote/demote | Per `05_APPROVAL_MATRIX.md` |

## Real adapters timeline
- Sandbox access arrives -> run task 012 real-adapter sub-tasks (12b TBO, 12c Riya) against the contract suite.
- Meta/WhatsApp verified -> enable real WhatsApp in staging with test numbers, then shadow.
- Paid API purchase happens after the Phase 2 demo, per the owner's plan.

## Risks to watch
Supplier API surprises · WhatsApp template/quality limits · scraper breakage · LLM cost or quality drift · staff adoption · consent data quality in the legacy customer list · scope creep (use `docs/BACKLOG.md`).
