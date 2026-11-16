---
id: "20261115-222745-incidentforge-llm-provider-openai-first-claude-later-via-pro"
title: "IncidentForge: LLM provider — OpenAI first, Claude later via ProviderAdapter"
category: "architecture"
date: "2026-11-15T22:27:45.860979+00:00"
tags: ["llm", "provider", "openai", "incidentforge"]
---

## Decision
MVP uses OpenAI (OpenAI-compatible endpoint). The ProviderAdapter protocol supports adding Anthropic Claude and Ollama in later iterations without changing domain code.

## Rationale
OpenAI has the most reliable tool-calling and structured output support. The ProviderAdapter abstraction means the choice is not permanent.

## Context
The ProviderAdapter Protocol is defined in contracts/interfaces.py. The first implementation will be `OpenAIProviderAdapter`. A second `AnthropicProviderAdapter` can be added as a P1 item. `.env.example` should include `OPENAI_API_KEY` and `OPENAI_MODEL` (e.g. `gpt-4o`).
