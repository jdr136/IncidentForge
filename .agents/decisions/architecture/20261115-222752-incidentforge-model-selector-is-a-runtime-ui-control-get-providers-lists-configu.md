---
id: "20261115-222752-incidentforge-model-selector-is-a-runtime-ui-control-get-pro"
title: "IncidentForge: Model selector is a runtime UI control; GET /providers lists configured providers"
category: "architecture"
date: "2026-11-15T22:27:52.742791+00:00"
tags: ["ui", "provider", "api", "incidentforge"]
---

## Decision
The model/provider selector is a runtime UI control. The overview page shows a dropdown of configured providers and their available models. POST /runs accepts `provider` and `model` in the request body (already in the spec's example request at §8.2). A GET /providers endpoint returns the list of providers configured via environment variables.

## Rationale
A runtime selector makes the demo more compelling — you can run the same scenario with different models and compare results side by side. The backend already passes provider/model through to the run record per the spec.

## Context
Backend must expose GET /providers returning configured providers and model IDs. The frontend model selector reads from this endpoint. Provider availability is determined by presence of the relevant API key in the server environment (e.g. OPENAI_API_KEY present → openai provider available). Never expose API keys to the Next.js client.
