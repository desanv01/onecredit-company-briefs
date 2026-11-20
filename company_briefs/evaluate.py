"""Check saved company briefs and prepare a paired human review without a model call."""

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "evaluation" / "manifest.json"
LOG_FIELDS = {
    "registrationNumber",
    "version",
    "status",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "latency_ms",
    "attempts",
    "fact_ids",
    "follow_up_id",
}


def reject_constant(value):
    raise ValueError(f"{value} is not a JSON value")


def read_objects(path):
    """Preserve physical line numbers for diagnostics and source review."""
    rows = []
    errors = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line, parse_constant=reject_constant)
            except ValueError as error:
                errors.append(f"{path}:{line_number}: {error}")
                continue
            if isinstance(row, dict):
                rows.append((line_number, row))
            else:
                errors.append(f"{path}:{line_number}: expected a JSON object")
    return rows, errors


def read_index(path, *, expect_summary=False, source_ids=None):
    rows, errors = read_objects(path)
    indexed = {}
    seen = set()
    for line, row in rows:
        registration = row.get("registrationNumber")
        if not isinstance(registration, str) or not registration.strip():
            errors.append(f"{path}:{line}: expected a nonempty registrationNumber")
            continue
        if registration in seen:
            errors.append(f"{path}:{line}: duplicate registrationNumber {registration}")
            continue
        seen.add(registration)
        if source_ids is not None and registration not in source_ids:
            errors.append(f"{path}:{line}: unknown registrationNumber {registration}")
            continue
        summary = row.get("summary")
        if expect_summary and (not isinstance(summary, str) or not summary.strip()):
            errors.append(f"{path}:{line}: expected a nonempty summary")
            continue
        indexed[registration] = (line, row)
    return indexed, errors


def strings(values):
    return isinstance(values, list) and all(isinstance(value, str) and value.strip() for value in values)


def read_manifest(path, dataset, dataset_digest, source):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a JSON object")
    if dataset_digest not in (manifest.get("dataset_sha256"), manifest.get("dataset_sha256_lf")):
        raise ValueError(f"dataset SHA-256 does not match the frozen manifest ({dataset}: {dataset_digest})")
    development = manifest.get("development_ids")
    cases = manifest.get("review_cases")
    if not strings(development) or not development or not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain nonempty development_ids and review_cases lists")
    review_ids = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("registrationNumber"), str):
            raise TypeError("each review case must have a registrationNumber")
        if not all(case.get(key) and strings(case[key]) for key in ("tags", "expected_facts", "traps")):
            raise ValueError("each review case must have nonempty tags, expected_facts and traps lists")
        review_ids.append(case["registrationNumber"])
    all_ids = development + review_ids
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("development and review IDs must be unique and disjoint")
    if unknown := set(all_ids) - source.keys():
        raise ValueError(f"manifest contains unknown registration IDs: {', '.join(sorted(unknown))}")
    return cases


def describe_outputs(label, path, indexed, source, review_ids, errors):
    word_counts = [len(row["summary"].split()) for _, row in indexed.values()]
    words = f", median words {statistics.median(word_counts):g}" if word_counts else ""
    print(
        f"{label} ({path}): {len(indexed)}/{len(source)} source records, "
        f"{len(review_ids & indexed.keys())}/{len(review_ids)} held-out records, "
        f"{len(errors)} JSONL/schema errors{words}",
        file=sys.stderr,
    )
    if missing := source.keys() - indexed.keys():
        print(f"  Missing: {', '.join(sorted(missing))}", file=sys.stderr)


def resolve_pointer(record, path):
    """Resolve a source pointer using RFC 6901 token escaping."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("expected a non-root JSON pointer")
    current = record
    for token in path[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise ValueError("invalid JSON pointer escape")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("invalid JSON pointer index")
            current = current[int(key)]
        else:
            current = current[key]
    return current


def trace_errors(record, ids, follow_up_id, summary):
    from .candidate import InvalidSelectionError, validate_selection
    from .evidence import build_facts
    from .render import render_summary

    facts = build_facts(record)
    try:
        selection = validate_selection(json.dumps({"fact_ids": ids, "follow_up_id": follow_up_id}), facts)
    except InvalidSelectionError as error:
        yield f"invalid candidate trace: {error}"
        return
    if render_summary(facts, selection.fact_ids, selection.follow_up_id) != summary:
        yield "candidate summary does not match its trace when rendered"
    by_id = {fact.id: fact for fact in facts}
    for fact_id in selection.fact_ids:
        fact = by_id[fact_id]
        if not fact.source_paths:
            yield f"fact {fact_id!r} has no source pointers"
        for path in fact.source_paths:
            try:
                resolve_pointer(record, path)
            except IndexError, KeyError, TypeError, ValueError:
                yield f"fact {fact_id!r} contains an unresolved source pointer {path!r}"


def log_schema_errors(row, source):
    if missing := LOG_FIELDS - row.keys():
        yield f"missing run log fields: {', '.join(sorted(missing))}"
    registration = row.get("registrationNumber")
    if not isinstance(registration, str) or registration not in source:
        yield "expected a known registrationNumber"
    version = row.get("version")
    if not isinstance(version, str) or not version.strip():
        yield "expected a nonempty version"
    if row.get("status") not in ("success", "fallback", "error"):
        yield "expected status success, fallback or error"
    model = row.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        yield "expected model to be a nonempty string or null"
    for field in ("prompt_tokens", "completion_tokens", "latency_ms", "attempts"):
        value = row.get(field)
        if value is None and field != "attempts":
            continue
        if type(value) is not int or value < 0:
            yield f"expected nonnegative integer {field}"
    ids = row.get("fact_ids")
    if ids is not None and (not strings(ids) or len(ids) != len(set(ids))):
        yield "expected unique string fact_ids or null"
    follow_up_id = row.get("follow_up_id")
    if follow_up_id is not None and (not isinstance(follow_up_id, str) or not follow_up_id.strip()):
        yield "expected follow_up_id to be a nonempty string or null"


def validate_logs(paths, source, baseline_path, candidate_path, baseline, candidate):
    errors = []
    by_version = defaultdict(list)
    seen_pairs = set()
    verified_traces = 0
    outputs = {baseline_path.stem: baseline, candidate_path.stem: candidate}
    for path in paths:
        rows, invalid_json = read_objects(path)
        errors.extend(invalid_json)
        for line, row in rows:
            prefix = f"{path}:{line}"
            if problems := list(log_schema_errors(row, source)):
                errors.extend(f"{prefix}: {problem}" for problem in problems)
                continue
            version = row["version"]
            registration = row["registrationNumber"]
            pair = (version, registration)
            if pair in seen_pairs:
                errors.append(f"{prefix}: duplicate run log entry for {version} / {registration}")
                continue
            seen_pairs.add(pair)
            by_version[version].append(row)
            if version not in outputs:
                continue
            has_summary = registration in outputs[version]
            if has_summary != (row["status"] in ("success", "fallback")):
                errors.append(f"{prefix}: {row['status']} is inconsistent with the saved summary for {registration}")
            if version == candidate_path.stem and has_summary:
                record = source[registration][1]
                summary = candidate[registration][1]["summary"]
                problems = list(trace_errors(record, row["fact_ids"], row["follow_up_id"], summary))
                errors.extend(f"{prefix}: {registration}: {problem}" for problem in problems)
                verified_traces += not problems

    print(f"Candidate traces: {verified_traces}/{len(candidate)} saved summaries verified", file=sys.stderr)
    for version, indexed in outputs.items():
        logged = {registration for logged_version, registration in seen_pairs if logged_version == version}
        if missing := indexed.keys() - logged:
            errors.append(f"Run log ({version}): no matching entry for {len(missing)}/{len(indexed)} saved summaries")
    for version, rows in sorted(by_version.items()):
        statuses = Counter(row["status"] for row in rows)
        print(
            f"Run log ({version}): {len(rows)} rows, {statuses['success']} success, "
            f"{statuses['fallback']} fallback, {statuses['error']} error, {sum(row['attempts'] for row in rows)} attempts",
            file=sys.stderr,
        )
        for field in ("prompt_tokens", "completion_tokens", "latency_ms"):
            measured = [row[field] for row in rows if row[field] is not None]
            if measured:
                aggregate = "median" if field == "latency_ms" else "sum"
                value = statistics.median(measured) if aggregate == "median" else sum(measured)
                print(f"  {field} {aggregate}: {value} ({len(measured)}/{len(rows)} reported)", file=sys.stderr)
    return errors


def paired_review(cases, source, source_path, baseline, candidate, *, reveal):
    # Balance the two orders, distributing them independently of manifest order.
    shuffled = sorted(cases, key=lambda case: hashlib.sha256(case["registrationNumber"].encode()).digest())
    candidate_first = {case["registrationNumber"] for case in shuffled[::2]}
    if reveal:
        lines = ["# Paired review key", "", "| Registration | A | B |", "| --- | --- | --- |"]
    else:
        lines = [
            "# Masked paired review",
            "",
            "Use the frozen expectations below and check each brief against the underlying company record. "
            "Apply the scores in evaluation/RUBRIC.md and record them before opening the review key.",
            "",
        ]
    for case in cases:
        registration = case["registrationNumber"]
        pair = [("Baseline", baseline), ("Candidate", candidate)]
        if registration in candidate_first:
            pair.reverse()
        if reveal:
            lines.append(f"| {registration} | {pair[0][0]} | {pair[1][0]} |")
            continue
        source_line = source[registration][0]
        lines.extend((f"## {registration}", "", f"Source record: {source_path}, line {source_line}.", ""))
        lines.extend((f"Tags: {', '.join(case['tags'])}", "", "Expected noteworthy facts", ""))
        lines.extend(f"- {fact}" for fact in case["expected_facts"])
        lines.extend(("", "Review caveats", ""))
        lines.extend(f"- {trap}" for trap in case["traps"])
        for label, (_, indexed) in zip(("A", "B"), pair, strict=True):
            summary = indexed[registration][1]["summary"].strip() if registration in indexed else "*Missing summary.*"
            lines.extend(("", f"### Brief {label}", "", summary))
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="source companies.jsonl")
    parser.add_argument("baseline", type=Path, help="baseline summaries JSONL")
    parser.add_argument("candidate", type=Path, help="candidate summaries JSONL")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--run-log", type=Path, action="append", default=[], help="optional run log JSONL, repeatable")
    parser.add_argument("--packet", type=Path, help="write the packet or reveal key as a UTF-8 Markdown file")
    parser.add_argument("--reveal", action="store_true", help="print the review key instead of the masked packet")
    args = parser.parse_args(argv)
    if args.run_log and args.baseline.stem == args.candidate.stem:
        parser.error("baseline and candidate filenames must have different stems when checking run logs")
    inputs = (args.data, args.baseline, args.candidate, args.manifest, *args.run_log)
    if args.packet and args.packet.resolve() in {path.resolve() for path in inputs}:
        parser.error("--packet must name a different file from the inputs")

    try:
        dataset_digest = hashlib.sha256(args.data.read_bytes()).hexdigest()
        source, errors = read_index(args.data)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        cases = read_manifest(args.manifest, args.data, dataset_digest, source)
        review_ids = {case["registrationNumber"] for case in cases}
        print(f"Verified dataset SHA-256 {dataset_digest}", file=sys.stderr)
        baseline, baseline_errors = read_index(args.baseline, expect_summary=True, source_ids=source.keys())
        candidate, candidate_errors = read_index(args.candidate, expect_summary=True, source_ids=source.keys())
        describe_outputs("Baseline", args.baseline, baseline, source, review_ids, baseline_errors)
        describe_outputs("Candidate", args.candidate, candidate, source, review_ids, candidate_errors)
        errors = baseline_errors + candidate_errors
        for name, indexed in (("Baseline", baseline), ("Candidate", candidate)):
            if missing := source.keys() - indexed.keys():
                errors.append(f"{name}: missing {len(missing)}/{len(source)} source records")
        if args.run_log:
            errors.extend(validate_logs(args.run_log, source, args.baseline, args.candidate, baseline, candidate))
        if errors:
            print("\n".join(errors), file=sys.stderr)
        packet = paired_review(cases, source, args.data, baseline, candidate, reveal=args.reveal)
        if args.packet:
            args.packet.parent.mkdir(parents=True, exist_ok=True)
            args.packet.write_text(packet, encoding="utf-8", newline="\n")
        else:
            print(packet, end="")
        return int(bool(errors))
    except (OSError, TypeError, ValueError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
