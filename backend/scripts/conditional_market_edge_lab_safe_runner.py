"""Compatibility runner for the conditional market-edge lab.

Some fixtures legitimately return None from an engine when that engine cannot
produce a scored result from the available market inputs. The research lab
should treat that as an unscorable observation, not crash the whole run.

This wrapper preserves the existing lab implementation and records the number
of None engine results in the JSON report as execution diagnostics. It does not
change production state or model rules.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import conditional_market_edge_lab as lab


def _output_path(argv: list[str]) -> Path:
    for i, arg in enumerate(argv):
        if arg == "--output" and i + 1 < len(argv):
            return Path(argv[i + 1])
    return Path("conditional_market_edge_lab.json")


async def main() -> None:
    skipped = {"bayesian_none": 0, "poisson_none": 0}

    original_bayesian = lab.bay_engine.analyse_fixture
    original_poisson = lab.poi_engine.analyse_fixture

    def safe_bayesian(*args, **kwargs):
        result = original_bayesian(*args, **kwargs)
        if result is None:
            skipped["bayesian_none"] += 1
            return SimpleNamespace(market_results=[])
        return result

    def safe_poisson(*args, **kwargs):
        result = original_poisson(*args, **kwargs)
        if result is None:
            skipped["poisson_none"] += 1
            return SimpleNamespace(results=[])
        return result

    lab.bay_engine.analyse_fixture = safe_bayesian
    lab.poi_engine.analyse_fixture = safe_poisson

    try:
        await lab.main()
    finally:
        target = _output_path(sys.argv[1:])
        if not target.is_absolute():
            target = Path.cwd() / target
        if target.is_file():
            try:
                report = json.loads(target.read_text(encoding="utf-8"))
                report["execution_diagnostics"] = {
                    "bayesian_none_fixtures": skipped["bayesian_none"],
                    "poisson_none_fixtures": skipped["poisson_none"],
                    "note": "None engine results were treated as unscorable observations; no production rules or DB state were changed.",
                }
                target.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(
                    "\nExecution diagnostics: "
                    f"Bayesian None={skipped['bayesian_none']}, "
                    f"Poisson None={skipped['poisson_none']}"
                )
            except (OSError, json.JSONDecodeError) as exc:
                print(f"\n[WARNING] Could not append execution diagnostics: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
