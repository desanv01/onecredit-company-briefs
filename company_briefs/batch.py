"""Generate, checkpoint and publish versioned company briefs."""

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from company_briefs.baseline import BASELINE_PROMPT_VERSION, generate_baseline
from company_briefs.candidate import (
    CANDIDATE_PROMPT_VERSION,
    InvalidSelectionError,
    fallback_selection,
    select_facts,
    validate_selection,
)
from company_briefs.evidence import build_facts
from company_briefs.provider import Completion, DeepSeekClient, ProviderError
from company_briefs.render import render_summary

VERSIONS = ("baseline", "evidence")
SOURCE_FILES = ("batch.py", "baseline.py", "candidate.py", "evidence.py", "provider.py", "render.py")


@dataclass(frozen=True)
class Job:
    version: str
    record: dict
    path: Path

    @property
    def company_id(self) -> str:
        return self.record["registrationNumber"]


def jsonl(rows) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def load_records(data: bytes) -> dict[str, dict]:
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    records = {row["registrationNumber"]: row for row in rows}
    if not records or len(records) != len(rows):
        raise ValueError("Dataset is empty or contains duplicate company IDs")
    if any(
        not company_id.startswith("SYN-") or len(company_id) != 10 or not company_id[4:].isdigit() for company_id in records
    ):
        raise ValueError("Dataset contains an invalid registration number")
    return records


def usage(completions: list[Completion], field: str) -> int | None:
    counts = [getattr(call, field) for call in completions]
    return sum(counts) if counts and all(count is not None for count in counts) else None


def generate(job: Job, client: DeepSeekClient) -> dict:
    facts = build_facts(job.record) if job.version == "evidence" else []
    start = time.perf_counter()
    completions = []
    errors = []
    selection = None
    summary = None
    status = "error"
    attempts = 0
    for attempt in range(2):
        attempts += 1
        try:
            if job.version == "baseline":
                completion = generate_baseline(client, job.record)
                completions.append(completion)
                summary = completion.text
            else:
                completion = select_facts(client, facts, correction=bool(attempt))
                completions.append(completion)
                selection = validate_selection(completion.text, facts)
                summary = render_summary(facts, selection.fact_ids, selection.follow_up_id)
        except ProviderError as exc:
            errors.append(str(exc))
            if not exc.retryable:
                break
            if attempt == 0:
                time.sleep(1)
        except InvalidSelectionError as exc:
            errors.append(str(exc))
        else:
            status = "success"
            break

    if status == "error" and job.version == "evidence":
        selection = fallback_selection(facts)
        summary = render_summary(facts, selection.fact_ids, selection.follow_up_id)
        status = "fallback"

    return {
        "registrationNumber": job.company_id,
        "version": job.version,
        "status": status,
        "model": completions[-1].model if completions else None,
        "prompt_tokens": usage(completions, "prompt_tokens"),
        "completion_tokens": usage(completions, "completion_tokens"),
        "latency_ms": round((time.perf_counter() - start) * 1000),
        "attempts": attempts,
        "errors": errors,
        "fact_ids": selection.fact_ids if selection else [],
        "follow_up_id": selection.follow_up_id if selection else None,
        "summary": summary,
    }


def run_identity(data: bytes, model: str) -> dict:
    base = Path(__file__).parent
    return {
        "dataset_sha256": hashlib.sha256(data).hexdigest(),
        "model_requested": model,
        "baseline_prompt_version": BASELINE_PROMPT_VERSION,
        "evidence_prompt_version": CANDIDATE_PROMPT_VERSION,
        "source_sha256": {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
    }


def execute(args: argparse.Namespace, client: DeepSeekClient) -> int:
    data = args.data.read_bytes()
    records = load_records(data)
    requested = sorted(dict.fromkeys(args.ids or records))
    if unknown := set(requested) - records.keys():
        raise ValueError(f"Unknown company IDs: {', '.join(sorted(unknown))}")
    versions = VERSIONS if args.version == "both" else (args.version,)
    if args.ids and args.output.resolve() == Path("summaries").resolve():
        raise ValueError("Supply a separate --output folder for a development subset")

    identity = run_identity(data, args.model)
    identity["company_ids"] = requested
    stamp = args.run_dir / "identity.json"
    if stamp.exists():
        if json.loads(stamp.read_text(encoding="utf-8")) != identity:
            raise ValueError("Run directory is bound to other data, code, configuration or company IDs")
    else:
        write_text(stamp, json.dumps(identity, indent=2) + "\n")

    jobs = [
        Job(version, records[company_id], args.run_dir / version / f"{company_id}.json")
        for company_id in requested
        for version in versions
    ]
    rows = {}
    remaining = []
    for job in jobs:
        if job.path.exists():
            previous = json.loads(job.path.read_text(encoding="utf-8"))
            if previous["status"] == "success" or (previous["status"] == "fallback" and not args.retry_fallbacks):
                rows[job.version, job.company_id] = previous
                continue
        remaining.append(job)

    if remaining:
        print(f"Generating {len(remaining)} briefs with {args.workers} workers.", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(generate, job, client): job for job in remaining}
        for future in as_completed(futures):
            job = futures[future]
            row = future.result()
            write_text(job.path, json.dumps(row, ensure_ascii=False, indent=2) + "\n")
            rows[job.version, job.company_id] = row
            print(f"{job.version} {job.company_id} {row['status']}", flush=True)

    failed = False
    logs = []
    for version in VERSIONS:
        version_rows = []
        for company_id in requested:
            checkpoint = args.run_dir / version / f"{company_id}.json"
            if checkpoint.exists():
                row = rows.get((version, company_id)) or json.loads(checkpoint.read_text(encoding="utf-8"))
                version_rows.append(row)
        logs.extend({key: value for key, value in row.items() if key != "summary"} for row in version_rows)
        if version not in versions:
            continue
        summaries = [
            {"registrationNumber": row["registrationNumber"], "summary": row["summary"]}
            for row in version_rows
            if row["summary"]
        ]
        if len(summaries) == len(requested):
            write_text(args.output / f"{version}.jsonl", jsonl(summaries))
        else:
            failed = True
            print(f"{version} incomplete ({len(summaries)}/{len(requested)}). Check the run log.", flush=True)

    write_text(args.run_dir / "run-log.jsonl", jsonl(logs))
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/companies.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("summaries"))
    parser.add_argument("--run-dir", type=Path, default=Path(".runs/full"))
    parser.add_argument("--version", choices=(*VERSIONS, "both"), default="both")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=4)
    parser.add_argument("--ids", nargs="+", help="Optional development subset. Use its own output and run folders.")
    parser.add_argument("--retry-fallbacks", action="store_true")
    args = parser.parse_args()
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key or key == "replace_me":
        parser.error("Set DEEPSEEK_API_KEY, for example through uv run --env-file .env.local.")
    try:
        return execute(args, DeepSeekClient(api_key=key, model=args.model))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
