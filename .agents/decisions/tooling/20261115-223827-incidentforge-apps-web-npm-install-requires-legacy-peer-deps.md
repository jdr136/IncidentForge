---
id: "20261115-223827-incidentforge-apps-web-npm-install-requires-legacy-peer-deps"
title: "IncidentForge: apps/web npm install requires --legacy-peer-deps"
category: "tooling"
date: "2026-11-15T22:38:27.342120+00:00"
tags: []
---

Next.js 15.0.3's peer dependency references React 19 RC (`19.0.0-rc-66855b96-20241106`), but npm resolves React 19 stable (19.3.1+). The stable release is fully compatible. Use `npm install --legacy-peer-deps` in apps/web to bypass the stale RC peer constraint. This is intentional and not a bug.