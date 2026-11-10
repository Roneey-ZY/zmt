"""Compare unassisted parsing and explicitly confirmed mappings on fixed fixtures."""

import argparse
import json
import os
import tomllib
from pathlib import Path
from time import perf_counter

from agent.main_agent import AgentRunError, configured_model
from agent.mapping_agent import known_mapping, suggest_mapping
from agent.tools import ReviewRun, confirm_table_mapping, run_rules
from core.demo import DATA, load_case
from core.parser import TableAnchor, inspect_tables
from scripts.evaluate import score_run, summarize


def compare(folders, live):
    before, after, model_records = [], [], []
    for folder in folders:
        truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
        spec = folder / "confirmation.json"
        steps = json.loads(spec.read_text(encoding="utf-8")) if spec.exists() else []
        run = ReviewRun(load_case(folder), "zh", "rules")
        start = perf_counter()
        run_rules(run)
        baseline_ms = (perf_counter() - start) * 1000
        baseline_checks = run.result.compared
        before.append(score_run(folder.name, truth, run, baseline_ms, None))
        confirmations = []
        applied_ms = 0.0
        error = None
        for step in steps:
            try:
                start = perf_counter()
                file = next(file for file in run.files if file.kind == step["kind"])
                anchor = TableAnchor(step["row"], step.get("sheet"), step.get("page"))
                preview = next((table for table in inspect_tables(file.name, file.data)
                                if table.anchor == anchor), None)
                if preview is None:
                    raise ValueError("预先指定的表头未生成候选。")
                expected = {int(column): field for column, field in step["extra"].items()}
                final = {**known_mapping(preview), **expected}
                run = confirm_table_mapping(run, file.kind, anchor, final)
                applied_ms += (perf_counter() - start) * 1000
                confirmations.append({
                    "kind": file.kind, "header": anchor.source(file.name).model_dump(),
                    "confirmed": final,
                })
                if live and expected:
                    provider, key, model = live
                    start = perf_counter()
                    try:
                        proposed = suggest_mapping(file.kind, preview, key, model, provider)
                        proposal_error = None
                    except AgentRunError as exc:
                        proposed, proposal_error = {}, str(exc)
                    model_records.append({
                        "case": folder.name, "kind": file.kind, "expected": expected,
                        "proposed": proposed, "error": proposal_error,
                        "correct": sum(expected.get(i) == field for i, field in proposed.items()),
                        "elapsed_ms": round((perf_counter() - start) * 1000, 3),
                    })
            except Exception as exc:
                error = str(exc)
                break
        record = score_run(folder.name, truth, run, baseline_ms + applied_ms, error)
        after.append({**record, "confirmations": confirmations,
                      "compared_before": baseline_checks, "compared_after": run.result.compared})
    return {"baseline": {"summary": summarize(before), "records": before},
            "confirmed": {"summary": summarize(after), "records": after}, "model": model_records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-ai", action="store_true")
    parser.add_argument("--output", type=Path, default=DATA / "template-evaluation.json")
    args = parser.parse_args()
    live = None
    if args.live_ai:
        path = DATA.parent / ".streamlit/secrets.toml"
        secrets = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        live = configured_model(lambda name, default="": os.getenv(name) or str(secrets.get(name, default)))
        if not live[1]:
            parser.error("No model credentials configured.")
    results = {}
    for subset in ("regression", "new-layouts"):
        folders = sorted(path.parent for path in (DATA / "template_validation" / subset).glob("*/truth.json"))
        results[subset] = compare(folders, live)
    if live:
        results["model"] = {"provider": live[0], "name": live[2]}
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        name: {key: result[key]["summary"] for key in ("baseline", "confirmed")} | {"model": result["model"]}
        for name, result in results.items() if name != "model"
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
