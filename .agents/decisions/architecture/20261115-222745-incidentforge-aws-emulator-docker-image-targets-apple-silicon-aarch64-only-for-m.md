---
id: "20261115-222745-incidentforge-aws-emulator-docker-image-targets-apple-silico"
title: "IncidentForge: AWS Emulator Docker image targets Apple Silicon (aarch64) only for MVP"
category: "architecture"
date: "2026-11-15T22:27:45.861020+00:00"
tags: ["aws-emulator", "docker", "apple-silicon", "incidentforge"]
---

## Decision
The AWS Emulator Dockerfile in infra/aws-emulator/ targets the aarch64 package only. Cross-platform support (x86_64) is not in scope for MVP. README documents Apple Silicon as the only supported local platform.

## Rationale
All contributors are on Apple Silicon Macs. Adding a BUILD_ARCH argument and multi-arch support adds complexity that is not needed now.

## Context
If a contributor on x86_64 wants to contribute later, the Dockerfile should be updated to accept a BUILD_ARCH build argument. Document this as a known limitation in the README.
