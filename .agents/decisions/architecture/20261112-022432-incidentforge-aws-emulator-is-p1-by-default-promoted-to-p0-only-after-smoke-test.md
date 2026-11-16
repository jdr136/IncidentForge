---
id: "20261112-022432-incidentforge-aws-emulator-is-p1-by-default-promoted-to-p0-o"
title: "IncidentForge: AWS Emulator is P1 by default; promoted to P0 only after smoke test passes"
category: "architecture"
date: "2026-11-12T02:24:32.307773+00:00"
tags: ["aws-emulator", "risk", "delivery", "incidentforge"]
---

## Decision
AWS Emulator work (IF-010, IF-011) is treated as P1 by default and only promoted to P0 if the emulator smoke test passes on Day 1. The delivery plan adds IF-000 (freeze shared interfaces) as a mandatory gate before all other work.

## Rationale
AWS Emulator was announced on 11 November 2026 — the day before this spec was written. A day-one product has higher-than-documented risk: Docker build may have undocumented requirements, S3 operation fidelity is unverified by anyone outside AWS, and documentation may not be stable. The spec's "Medium/Medium" risk rating is too low; revised to High/Medium. The minimum credible demo is one Kubernetes scenario, not AWS.

## Context
The original spec listed AWS Emulator as "conditional P0." This decision makes it a firm P1 default. The mitigation (smoke gate, UNSUPPORTED path) in the spec remains correct.