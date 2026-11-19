#!/usr/bin/env python3
"""
IncidentForge benchmark runner.

Runs each scenario N times with each configured provider, triggers
verification, collects results, and reports accuracy metrics.

Usage:
    python3 scripts/benchmark.py [--trials N] [--provider ID] [--scenario ID]
                                  [--no-verify] [--api URL] [--output FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def api_get(base: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read())


def api_post(base: str, path: str, body: dict | None = None) -> Any:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body_text}") from exc


# ── Run lifecycle helpers ─────────────────────────────────────────────────────

TERMINAL = {
    "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED",
    "INTERRUPTED", "UNSUPPORTED", "INVALID_SCENARIO",
}

def wait_for(base: str, run_id: str, target_statuses: set[str],
             timeout: int = 180, poll: float = 3.0) -> dict:
    """Poll GET /runs/{id} until status is in target_statuses or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api_get(base, f"/api/v1/runs/{run_id}")
        status = run["status"]
        if status in target_statuses:
            return run
        if status in TERMINAL:
            return run  # ended unexpectedly
        time.sleep(poll)
    raise TimeoutError(f"Run {run_id} did not reach {target_statuses} within {timeout}s")


def extract_diagnosis(export: dict) -> dict | None:
    """Pull the diagnosis dict from the run export events."""
    for ev in reversed(export.get("events", [])):
        if ev.get("type") == "diagnosis_completed":
            d = ev.get("payload", {}).get("diagnosis")
            if d:
                return d
    return None


def extract_evaluation(export: dict) -> dict | None:
    """Pull the evaluation dict from the run export events."""
    for ev in reversed(export.get("events", [])):
        if ev.get("type") == "evaluation_completed":
            return ev.get("payload", {}).get("evaluation")
    return None


# ── Single trial ──────────────────────────────────────────────────────────────

def run_trial(
    base: str,
    scenario_id: str,
    provider_id: str,
    model: str,
    trial_num: int,
    verify: bool,
) -> dict:
    """Run one trial and return a result dict."""
    result: dict[str, Any] = {
        "scenario_id": scenario_id,
        "provider": provider_id,
        "model": model,
        "trial": trial_num,
        "status": "ERROR",
        "correct": False,
        "confidence": None,
        "tool_calls": None,
        "duration_ms": None,
        "evidence_valid": None,
        "verification_status": None,
        "root_cause_id": None,
        "component_scores": {},
        "error": None,
    }

    try:
        # Start run
        run = api_post(base, "/api/v1/runs", {
            "scenario_id": scenario_id,
            "scenario_version": 1,
            "provider": provider_id,
            "model": model,
            "temperature": 0,
        })
        run_id = run["run_id"]

        # Wait for AWAITING_VERIFICATION or terminal
        wait_target = {"AWAITING_VERIFICATION"} | TERMINAL
        run = wait_for(base, run_id, wait_target, timeout=180)
        result["status"] = run["status"]

        # Trigger verification if requested
        if verify and run["status"] == "AWAITING_VERIFICATION":
            api_post(base, f"/api/v1/runs/{run_id}/verify")
            run = wait_for(base, run_id, TERMINAL, timeout=120)
            result["status"] = run["status"]

        # Collect results from export
        export = api_get(base, f"/api/v1/runs/{run_id}/export")
        diagnosis = extract_diagnosis(export)
        evaluation = extract_evaluation(export)

        if diagnosis:
            result["root_cause_id"] = diagnosis.get("root_cause_id")
            result["confidence"] = diagnosis.get("confidence")

        if evaluation:
            result["verification_status"] = evaluation.get("verification_status")
            # Extract component scores
            scores = {}
            for key, val in evaluation.items():
                if isinstance(val, dict) and "score" in val and "max" in val:
                    scores[key] = val
            result["component_scores"] = scores

            # Determine correctness from root_cause score
            rc = scores.get("root_cause_accuracy", {})
            if rc.get("status") == "scored":
                result["correct"] = rc.get("score", 0) == rc.get("max", 0)
            elif result["root_cause_id"] not in (None, "UNKNOWN", ""):
                # Stub mode — can't truly verify correctness
                result["correct"] = None  # unknown

            ev = scores.get("evidence_quality", {})
            result["evidence_valid"] = ev.get("score", 0) > 0 if ev else None

        # Count tool calls from events
        tool_calls = sum(
            1 for ev in export.get("events", [])
            if ev.get("type") == "tool_observation"
        )
        result["tool_calls"] = tool_calls

        # Duration from first to last event
        events = export.get("events", [])
        if len(events) >= 2:
            try:
                from datetime import datetime as dt
                t0 = dt.fromisoformat(events[0]["at"].replace("Z", "+00:00"))
                t1 = dt.fromisoformat(events[-1]["at"].replace("Z", "+00:00"))
                result["duration_ms"] = int((t1 - t0).total_seconds() * 1000)
            except Exception:
                pass

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_results(all_results: list[dict], providers: list[dict]) -> None:
    """Print a formatted benchmark report to stdout."""
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print(f"\n{BOLD}IncidentForge Benchmark Results{RESET}")
    print(f"{'─' * 60}")
    print(f"Date:      {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    # Group by provider × scenario
    by_provider: dict[str, dict[str, list[dict]]] = {}
    for r in all_results:
        key = f"{r['provider']}/{r['model']}"
        by_provider.setdefault(key, {}).setdefault(r["scenario_id"], []).append(r)

    for provider_key, scenarios in sorted(by_provider.items()):
        print(f"{BOLD}{CYAN}{provider_key}{RESET}")
        print(f"{'─' * 40}")

        total_correct = 0
        total_runs = 0
        total_verified = 0
        total_verifiable = 0
        all_tools: list[int] = []
        all_durations: list[int] = []

        for scenario_id, trials in sorted(scenarios.items()):
            print(f"\n  {BOLD}{scenario_id}{RESET}")
            for t in trials:
                correct_sym = (
                    f"{GREEN}✓{RESET}" if t["correct"] is True
                    else f"{RED}✗{RESET}" if t["correct"] is False
                    else f"{YELLOW}~{RESET}"
                )
                verify_sym = ""
                if t["verification_status"] == "passed":
                    verify_sym = f"  fix {GREEN}VERIFIED{RESET}"
                elif t["verification_status"] == "failed":
                    verify_sym = f"  fix {RED}FAILED{RESET}"
                elif t["verification_status"] == "not_evaluated":
                    verify_sym = f"  fix {YELLOW}not verified{RESET}"

                rc = t.get("root_cause_id") or "UNKNOWN"
                conf = f"{t['confidence']*100:.0f}%" if t["confidence"] is not None else "?%"
                tools = f"{t['tool_calls']} tools" if t["tool_calls"] is not None else ""
                dur = f"{t['duration_ms']/1000:.1f}s" if t["duration_ms"] else ""
                err = f"  {RED}ERROR: {t['error'][:50]}{RESET}" if t.get("error") else ""

                print(f"    Trial {t['trial']}: {correct_sym}  {rc:<35} conf={conf}  {tools}  {dur}{verify_sym}{err}")

                if t["correct"] is not None:
                    total_runs += 1
                    if t["correct"]:
                        total_correct += 1
                if t["verification_status"] in ("passed", "failed"):
                    total_verifiable += 1
                    if t["verification_status"] == "passed":
                        total_verified += 1
                if t["tool_calls"] is not None:
                    all_tools.append(t["tool_calls"])
                if t["duration_ms"] is not None:
                    all_durations.append(t["duration_ms"])

        print(f"\n{BOLD}  Summary{RESET}")
        if total_runs:
            acc_pct = total_correct / total_runs * 100
            acc_col = GREEN if acc_pct >= 80 else YELLOW if acc_pct >= 50 else RED
            print(f"  Root-cause accuracy:   {acc_col}{total_correct}/{total_runs} ({acc_pct:.0f}%){RESET}")
        if total_verifiable:
            fix_pct = total_verified / total_verifiable * 100
            fix_col = GREEN if fix_pct >= 80 else YELLOW if fix_pct >= 50 else RED
            print(f"  Verified fix rate:     {fix_col}{total_verified}/{total_verifiable} ({fix_pct:.0f}%){RESET}")
        if all_tools:
            median_tools = sorted(all_tools)[len(all_tools) // 2]
            print(f"  Median tool calls:     {median_tools}")
        if all_durations:
            median_dur = sorted(all_durations)[len(all_durations) // 2]
            print(f"  Median duration:       {median_dur/1000:.1f}s")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="IncidentForge benchmark runner")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--trials", type=int, default=3, help="Trials per scenario/provider")
    parser.add_argument("--provider", action="append", dest="providers", metavar="ID",
                        help="Provider to test (repeat for multiple; default: all available)")
    parser.add_argument("--scenario", action="append", dest="scenarios", metavar="ID",
                        help="Scenario to run (repeat for multiple; default: all)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip fix verification (faster, skips remediation score)")
    parser.add_argument("--output", metavar="FILE",
                        help="Save JSON results to FILE (default: data/benchmark_<ts>.json)")
    args = parser.parse_args()

    base = args.api.rstrip("/")

    # Check API is reachable
    try:
        health = api_get(base, "/health")
        print(f"API: {health.get('status', 'ok')} (v{health.get('version', '?')})")
    except Exception as exc:
        print(f"ERROR: Cannot reach API at {base} — {exc}")
        print("Run 'make dev-api' first.")
        sys.exit(1)

    # Get available scenarios
    all_scenarios = api_get(base, "/api/v1/scenarios")
    if args.scenarios:
        scenarios = [s for s in all_scenarios if s["id"] in args.scenarios]
        missing = set(args.scenarios) - {s["id"] for s in scenarios}
        if missing:
            print(f"ERROR: Unknown scenarios: {missing}")
            sys.exit(1)
    else:
        scenarios = all_scenarios

    # Get available providers
    all_providers = api_get(base, "/api/v1/providers")
    available = [p for p in all_providers if p["available"]]
    if args.providers:
        providers = [p for p in available if p["provider_id"] in args.providers]
        missing = set(args.providers) - {p["provider_id"] for p in providers}
        if missing:
            unavail = set(args.providers) - {p["provider_id"] for p in all_providers}
            if unavail:
                print(f"ERROR: Unknown providers: {unavail}")
            else:
                print(f"ERROR: Providers not configured (no API key): {missing}")
            sys.exit(1)
    else:
        providers = available

    if not providers:
        print("ERROR: No LLM providers configured.")
        print("Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env and restart the API.")
        sys.exit(1)

    if not scenarios:
        print("ERROR: No scenarios found.")
        sys.exit(1)

    total = len(providers) * len(scenarios) * args.trials
    print(f"\nRunning {total} trial(s): {len(scenarios)} scenario(s) × "
          f"{len(providers)} provider(s) × {args.trials} trial(s)")
    print(f"Verify: {'yes' if not args.no_verify else 'no'}\n")

    all_results = []
    run_num = 0

    for provider in providers:
        pid = provider["provider_id"]
        model = provider["models"][0]

        for scenario in scenarios:
            sid = scenario["id"]

            for trial in range(1, args.trials + 1):
                run_num += 1
                print(f"[{run_num}/{total}] {pid}/{model}  {sid}  trial {trial} ...", end=" ", flush=True)
                t0 = time.monotonic()
                result = run_trial(base, sid, pid, model, trial, verify=not args.no_verify)
                elapsed = time.monotonic() - t0

                # One-line status
                status = result["status"]
                correct = "✓" if result["correct"] is True else "✗" if result["correct"] is False else "~"
                verify = f"  fix={result['verification_status'] or 'n/a'}"
                print(f"{correct}  {status:<25}  {elapsed:.1f}s{verify}")

                all_results.append(result)

    # Print full report
    print_results(all_results, providers)

    # Save JSON
    output_path = args.output
    if not output_path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        output_path = f"data/benchmark_{ts}.json"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "benchmark_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "api": base,
            "trials": args.trials,
            "verify": not args.no_verify,
            "runs": all_results,
        }, f, indent=2)
    print(f"Results saved → {output_path}")


if __name__ == "__main__":
    main()
