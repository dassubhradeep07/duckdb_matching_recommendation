"""Scan existing raw and master CSV reports using configurable Soda contracts."""

import argparse
from collections import Counter
from contextlib import redirect_stdout
import csv
from datetime import datetime, timezone
import hashlib
from html import escape
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile

import duckdb
import yaml

# Keep local verification local; never publish to Soda Cloud.
os.environ.setdefault("SODA_CORE_TELEMETRY_ENABLED", "false")
from soda_core.common.yaml import ContractYamlSource, DataSourceYamlSource
from soda_core.contracts.contract_verification import ContractVerificationSession

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name("datasets.yaml")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve(base, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_config(path):
    config = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("DQ configuration must have version: 1")
    datasets = config.get("datasets")
    if not isinstance(datasets, dict) or not datasets:
        raise ValueError("DQ configuration needs a nonempty datasets mapping")
    for name, entry in datasets.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"Invalid dataset name: {name}")
        if not isinstance(entry, dict) or entry.get("layer") not in ("raw", "master"):
            raise ValueError(f"Invalid layer for {name}")
        for key in ("path", "contract"):
            if not isinstance(entry.get(key), str):
                raise ValueError(f"Missing {key} for {name}")
    return config


def scan(config_path, layer):
    config = load_config(config_path)
    config_dir = config_path.parent
    data_root = resolve(config_dir, config.get("project_root", ".."))
    entries = config["datasets"]
    selected = {name: entry for name, entry in entries.items()
                if layer == "all" or entry["layer"] == layer}
    if not selected:
        raise ValueError(f"No datasets configured for layer {layer}")
    # Raw data is also loaded for master-layer relationship/reconciliation checks.
    needed = {name: entry for name, entry in entries.items()
              if name in selected or entry["layer"] == "raw"}
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine": "Soda Core", "engine_version": version("soda-core"),
        "config": str(config_path), "config_sha256": sha256(config_path),
        "requested_layer": layer,
        "master_definition": "Existing matching reports; no golden records or merges are created.",
        "datasets": [], "checks": [], "errors": [], "inputs_unchanged": True,
    }
    fingerprints = {}
    loaded = {}
    with tempfile.TemporaryDirectory(prefix="soda-customer-dq-") as folder:
        database = Path(folder) / "snapshot.duckdb"
        with duckdb.connect(str(database)) as connection:
            for name, entry in needed.items():
                source = resolve(data_root, entry["path"])
                info = {"dataset": name, "layer": entry["layer"], "path": str(source)}
                try:
                    fingerprints[source] = sha256(source)
                    info["sha256"] = fingerprints[source]
                    connection.execute(
                        f'CREATE TABLE "{name}" AS SELECT * FROM read_csv(?, header=true, all_varchar=true)',
                        [str(source)],
                    )
                    info["row_count"] = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                    info["columns"] = [row[0] for row in connection.execute(f'DESCRIBE "{name}"').fetchall()]
                    loaded[name] = info
                except (OSError, duckdb.Error) as error:
                    info.update(status="ERROR", error=str(error))
                    report["errors"].append(f"{name}: {error}")
                if name in selected:
                    report["datasets"].append(info)

        source_yaml = yaml.safe_dump({
            "name": "customer_dq", "type": "duckdb",
            "connection": {"database": str(database), "read_only": True, "schema": "main"},
        })
        for name, entry in selected.items():
            if name not in loaded:
                continue
            info = loaded[name]
            contract_path = resolve(config_dir, entry["contract"])
            try:
                contract = yaml.safe_load(contract_path.read_text(encoding="utf-8-sig"))
                if not isinstance(contract, dict) or contract.get("dataset") != f"customer_dq/main/{name}":
                    raise ValueError(f"Contract must target customer_dq/main/{name}")
                if not contract.get("checks") and not any(c.get("checks") for c in contract.get("columns", [])):
                    raise ValueError("Contract has no checks")
                info.update(contract=str(contract_path), contract_sha256=sha256(contract_path))
                # Soda logs may contain schema details; keep stdout for our summary.
                with redirect_stdout(sys.stderr):
                    result = ContractVerificationSession.execute(
                        contract_yaml_sources=[ContractYamlSource.from_file_path(str(contract_path))],
                        data_source_yaml_sources=[DataSourceYamlSource.from_str(source_yaml)],
                        soda_cloud_publish_results=False,
                    )
                results = result.contract_verification_results
                if result.has_errors or len(results) != 1:
                    raise RuntimeError(result.get_errors_str() or "Soda did not return one contract result")
                actual = results[0]
                if not actual.check_results:
                    raise RuntimeError("Soda returned no evaluated checks")
                info["status"] = actual.status.value
                for checked in actual.check_results:
                    check = checked.check
                    report["checks"].append({
                        "layer": entry["layer"], "dataset": name,
                        "check": check.name or check.relative_path,
                        "column": check.column_name, "type": check.type,
                        "outcome": checked.outcome.value,
                        "value": checked.threshold_value,
                        "threshold": str(check.threshold) if check.threshold else "",
                        "diagnostics": checked.diagnostic_metric_values,
                    })
            except Exception as error:
                # Preserve reports for the other datasets when a contract is broken.
                info.update(status="ERROR", error=str(error))
                report["errors"].append(f"{name}: {error}")

    for source, before in fingerprints.items():
        try:
            unchanged = sha256(source) == before
        except OSError:
            unchanged = False
        if not unchanged:
            report["inputs_unchanged"] = False
            report["errors"].append(f"Input changed during scan: {source}; rerun on a stable snapshot")
    counts = Counter(row["outcome"] for row in report["checks"])
    report["summary"] = dict(counts)
    report["status"] = ("ERROR" if report["errors"] or counts["NOT_EVALUATED"] else
                        "FAILED" if counts["FAILED"] else
                        "WARNED" if counts["WARN"] else "PASSED")
    return report


def html_report(report, layer=None):
    checks = [c for c in report["checks"] if layer is None or c["layer"] == layer]
    datasets = [d for d in report["datasets"] if layer is None or d["layer"] == layer]
    counts = Counter(c["outcome"] for c in checks)
    h = lambda value: escape(str(value if value is not None else ""))
    cards = "".join(f'<div class="card"><strong>{counts[key]}</strong>{label}</div>'
                    for key, label in [("PASSED", "Passed"), ("WARN", "Warnings"), ("FAILED", "Failed"), ("NOT_EVALUATED", "Not evaluated")])
    dataset_rows = "".join("<tr>" + "".join(f"<td>{h(d.get(k, ''))}</td>" for k in
                            ("layer", "dataset", "row_count", "status", "path")) + "</tr>" for d in datasets)
    rows = "".join(f'<tr class="{h(c["outcome"])}">' + "".join(f"<td>{h(c.get(k))}</td>" for k in
                   ("layer", "dataset", "check", "outcome", "value", "threshold")) + "</tr>" for c in checks)
    errors = "".join(f"<li>{h(error)}</li>" for error in report["errors"])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Customer data quality report</title>
<style>body{{font:15px system-ui;margin:32px;color:#182735;background:#f5f7fa}}h1{{margin-bottom:8px}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}}.card{{background:white;padding:18px 28px;border-radius:8px}}
strong{{display:block;font-size:30px}}table{{border-collapse:collapse;width:100%;background:white;margin-bottom:28px}}
th,td{{padding:11px;text-align:left;border-bottom:1px solid #dce2e9;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#203c53;color:white}}.FAILED{{background:#fff0ee}}.WARN{{background:#fff8df}}
.NOT_EVALUATED{{background:#eee}}.scroll{{overflow:auto}}li{{margin:8px 0}}</style>
<h1>Customer data quality · {h(layer or 'raw + master')}</h1>
<p>Soda Core {h(report['engine_version'])} · Generated {h(report['generated_at_utc'])}</p>
<p>Run status: <b>{h(report['status'])}</b> · Input files unchanged: <b>{h(report['inputs_unchanged'])}</b></p>
<p>Master layer means existing matching reports. Scores and auto-merge candidates are recommendations, not verified identities.</p>
<div class="cards">{cards}</div><h2>Datasets</h2><div class="scroll"><table>
<tr><th>Layer</th><th>Dataset</th><th>Rows</th><th>Status</th><th>Input path</th></tr>{dataset_rows}</table></div>
<h2>Checks</h2><div class="scroll"><table><tr><th>Layer</th><th>Dataset</th><th>Rule</th><th>Outcome</th><th>Measured value</th><th>Threshold</th></tr>{rows}</table></div>
<h2>Execution errors</h2><ul>{errors or '<li>None</li>'}</ul>
<p>Missing-file, contract, and SQL errors are execution errors, not data-quality passes. Reports contain aggregate measurements, not customer rows.</p></html>'''


def write_reports(report, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "dq_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for layer in (None, "raw", "master"):
        if layer and not any(d["layer"] == layer for d in report["datasets"]):
            continue
        filename = "dq_report.html" if layer is None else f"dq_{layer}.html"
        (output / filename).write_text(html_report(report, layer), encoding="utf-8")
    fields = ["layer", "dataset", "check", "column", "type", "outcome", "value", "threshold", "diagnostics"]
    with (output / "dq_checks.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for check in report["checks"]:
            writer.writerow({**check, "diagnostics": json.dumps(check["diagnostics"], default=str)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--layer", choices=["raw", "master", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("reports"))
    parser.add_argument("--fail-on-quality", action="store_true", help="Exit 1 on failed checks; reports are still generated")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        config_path = args.config.resolve()
        output = args.output_dir.resolve()
        # Keep generated files away from source CSVs, contracts, and the pipeline.
        config = load_config(config_path)
        root = resolve(config_path.parent, config.get("project_root", ".."))
        protected = [config_path] + [resolve(root, e["path"]) for e in config["datasets"].values()]
        protected += [resolve(config_path.parent, e["contract"]) for e in config["datasets"].values()]
        names = {"dq_report.json", "dq_report.html", "dq_raw.html", "dq_master.html", "dq_checks.csv"}
        if any(p.parent == output and p.name in names for p in protected):
            raise ValueError("Output directory would overwrite an input or configuration file")
        report = scan(config_path, args.layer)
        write_reports(report, output)
        print(json.dumps({"status": report["status"], "summary": report["summary"],
                          "report": str(output / "dq_report.html"), "errors": report["errors"]}))
        if report["status"] == "ERROR":
            return 2
        return 1 if args.fail_on_quality and report["status"] == "FAILED" else 0
    except Exception as error:
        print(f"DQ execution error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
