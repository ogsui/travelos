# Task 030 — SEO content with fact-checking

Phase 4 · Depends on: 027, 029 · Requirements: SEO-2, SEO-3

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Search Console access.

## Scope
- Topic ideas from Search Console and own inquiries; destination guides, FAQs, visa pages drafted with required sources stored.
- Fact-check queue for humans; uniqueness/quality checks; sitemap, schema.org, refresh jobs.
- Throttle publishing volume to avoid thin/scaled content.

## Acceptance criteria
- Pages without sources cannot be published (test).
- Duplicate/near-duplicate detection.
- Visa/legal claims require staff verification.

## Human gates / approvals
- Publish A1 always.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-030: ...`, write the report from AGENTS.md, then STOP.
