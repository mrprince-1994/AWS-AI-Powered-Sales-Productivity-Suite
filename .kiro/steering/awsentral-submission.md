---
inclusion: auto
description: Conventions for end-of-day AWSentral submission (activities, SIFT, opportunity updates)
---

# AWSentral End-of-Day Submission — Conventions

When processing activity_queue and sift_queue JSON files for AWSentral submission, follow these rules:

## Opportunity Details Format

When updating the `opportunityDetails` field on an opportunity:

1. **Prefix every entry with "MP"** (Michael Prince's initials) followed by the date and a dash:
   - Format: `MP {M/D} - {concise summary of the call}`
   - Example: `MP 3/31 - Architecture review: Coach AI hooks and memory working. Two blockers identified...`

2. **Append, never overwrite.** If `opportunityDetails` already has content, add the new dated entry on a new line below the existing text. Only remove older entries if you hit a character limit and need to fit the latest update.

3. **Keep each entry concise** — one to three sentences capturing the key outcome, decisions, and next steps from the call.

## GenAI/ML Tag Selection

- **AGS-Specialist-GenAI/ML-Leading** (`aNgRU0000001t7J0AQ`): Use when the call notes contain clear, defined next steps with SA involvement (e.g., architecture reviews, build sessions, deliverables).
- **AGS-Specialist-GenAI/ML-Supporting** (`aNgRU0000001zsf0AA`): Use when the call was internal-only, advisory, or has no clear SA-driven next steps.
- If the opportunity already has either tag, leave it as-is.

## MEDDPICC Updates

- Only populate MEDDPICC fields that are currently empty — never overwrite existing values.
- If a field already has content, APPEND new evidence on a new line prefixed with the call date (e.g. `3/31: Customer confirmed 3x ROI target`).
- Keep each field under 500 characters.
- Only apply to Utility-type opportunities in standard stages (Prospect, Qualified, Technical Validation, Business Validation, Committed).
- Field mapping from MEDDPICC element names to Salesforce API field names:
  - Metrics → `metrics`
  - Economic Buyer → `economicBuyer`
  - Decision Criteria → `decisionCriteria`
  - Decision Process → `decisionProcess`
  - Paper Process → `paperProcess`
  - Implicate the Pain → `implicateThePain`
  - Champion → `champion`
  - Competition → `competition`
- MEDDPICC data is cumulative across calls — each call adds new evidence to build a complete picture over time.
- MEDDPICC queue files are in `call_notes_app/meddpicc_queue/` with the format: `{"customer_name", "call_date", "coverage": {"Element": "evidence string"}, "coverage_count", "total_elements"}`.

## Tracker

After creating each task, call `generate_opp_team_tracker.append_opportunity` with the task details.
