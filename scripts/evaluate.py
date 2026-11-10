import argparse
import json
import os
import statistics
import tomllib
from collections import Counter
from pathlib import Path
from time import perf_counter

from agent.main_agent import DEFAULT_MODEL, configured_model, run_agent
from agent.tools import ReviewRun, run_rules
from core.demo import DATA, load_case
from core.schemas import HEADER_FIELDS, ITEM_FIELDS


def expected_fields(truth):
    fields = {}
    for kind, rows in truth["documents"].items():
        for index, row in enumerate(rows):
            for key, value in row.items():
                if key in HEADER_FIELDS:
                    fields.setdefault((kind, -1, key), value.strip())
                elif key in ITEM_FIELDS:
                    fields[kind, index, key] = value.strip()
    return fields


def observed_fields(documents):
    fields = {}
    for doc in documents:
        for key, value in doc.header.items():
            fields[doc.kind, -1, key] = value.raw if value.source.file == doc.file else None
        for index, item in enumerate(doc.items):
            for key, value in item.fields.items():
                fields[doc.kind, index, key] = value.raw if value.source.file == doc.file else None
    return fields


def split_findings(rows, review):
    return Counter(tuple(row) for row in rows if (row[1] == "review") == review)


def score(truth, actual):
    return dict(tp=sum((truth & actual).values()), fp=sum((actual - truth).values()),
                fn=sum((truth - actual).values()))


def score_run(case, truth, run, elapsed, error):
    expected = expected_fields(truth)
    actual = observed_fields(run.documents or [])
    differences = [
        {"field": field, "expected": expected.get(field), "actual": actual.get(field)}
        for field in sorted(expected.keys() | actual.keys()) if expected.get(field) != actual.get(field)
    ]
    findings = [(f.rule, f.category, f.sku) for f in run.result.findings] if run.result else []
    anomaly = score(split_findings(truth["findings"], False), split_findings(findings, False))
    review = score(split_findings(truth["findings"], True), split_findings(findings, True))
    success = error is None and bool(run.report) and not differences and not (
        anomaly["fp"] or anomaly["fn"] or review["fp"] or review["fn"]
    )
    return dict(case=case, elapsed_ms=round(elapsed, 3), success=success,
                expected_findings=truth["findings"], actual_findings=findings,
                field_differences=differences, field_correct=len(expected.keys() | actual.keys()) - len(differences),
                field_count=len(expected.keys() | actual.keys()),
                anomaly=anomaly, review=review, error=error, tools=run.log)


def evaluate(mode, folders, key="", *, provider="openai", model=DEFAULT_MODEL):
    records = []
    for folder in folders:
        truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
        run = ReviewRun(load_case(folder), "zh", mode)
        started = perf_counter()
        error = None
        try:
            if mode == "ai":
                run_agent(run, key, model, provider=provider)
            else:
                run_rules(run)
        except Exception as exc:
            error = str(exc)
        elapsed = (perf_counter() - started) * 1000
        records.append(score_run(folder.name, truth, run, elapsed, error))
    return records


def summarize(records):
    def ratio(a, b):
        return round(a / b, 5) if b else None

    result = {
        "cases": len(records),
        "end_to_end_success_rate": ratio(sum(r["success"] for r in records), len(records)),
        "field_accuracy": ratio(sum(r["field_correct"] for r in records),
                                sum(r["field_count"] for r in records)),
        "mean_ms": round(statistics.mean(r["elapsed_ms"] for r in records), 3),
        "median_ms": round(statistics.median(r["elapsed_ms"] for r in records), 3),
    }
    for group in ("anomaly", "review"):
        totals = {key: sum(r[group][key] for r in records) for key in ("tp", "fp", "fn")}
        tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
        result[group] = {**totals, "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn)}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rules", "ai", "both"], default="rules")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=Path("evaluation.json"))
    args = parser.parse_args()
    folders = sorted(path.parent for path in (DATA / "benchmark").glob("*/truth.json"))
    if args.limit is not None:
        folders = folders[:args.limit]
    if not folders:
        parser.error("No cases selected. Run python -m scripts.generate_demo_data --benchmark.")
    modes = ["rules", "ai"] if args.mode == "both" else [args.mode]
    if "ai" in modes:
        path = DATA.parent / ".streamlit/secrets.toml"
        try:
            secrets = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        except (ValueError, OSError):
            parser.error("无法读取项目的 .streamlit/secrets.toml，请检查 TOML 格式。")
        provider, key, model = configured_model(
            lambda name, default="": os.environ.get(name) or str(secrets.get(name, default))
        )
    results = {}
    for mode in modes:
        if mode == "ai":
            if not key:
                results[mode] = {"skipped": "DEEPSEEK_API_KEY and OPENAI_API_KEY are not set"}
                continue
            records = evaluate(mode, folders, key, provider=provider, model=model)
        else:
            records = evaluate(mode, folders)
        results[mode] = {"summary": summarize(records), "records": records}
        if mode == "ai":
            results[mode].update(provider=provider, model=model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({mode: item.get("summary", item) for mode, item in results.items()},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
