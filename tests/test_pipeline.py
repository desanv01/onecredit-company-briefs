import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from company_briefs.batch import execute, load_records
from company_briefs.candidate import InvalidSelectionError, fallback_selection, validate_selection
from company_briefs.evaluate import trace_errors
from company_briefs.evidence import build_facts
from company_briefs.provider import Completion
from company_briefs.render import render_summary

DATA = Path("data/companies.jsonl")


class PipelineTests(unittest.TestCase):
    def test_fallback_preserves_every_required_fact_and_trace(self):
        records = load_records(DATA.read_bytes())
        self.assertEqual(len(records), 50)
        for company_id, record in records.items():
            with self.subTest(company_id=company_id):
                facts = build_facts(record)
                selection = fallback_selection(facts)
                summary = render_summary(facts, selection.fact_ids, selection.follow_up_id)
                self.assertFalse(list(trace_errors(record, selection.fact_ids, selection.follow_up_id, summary)))

    def test_incomplete_selection_is_rejected(self):
        record = load_records(DATA.read_bytes())["SYN-000545"]
        with self.assertRaises(InvalidSelectionError):
            validate_selection('{"fact_ids":["company"],"follow_up_id":null}', build_facts(record))

    def test_resume_retains_provider_and_fallback_outcomes(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            args = argparse.Namespace(
                data=DATA,
                output=root / "summaries",
                run_dir=root / "run",
                version="both",
                ids=["SYN-000545"],
                model="deepseek-flash",
                workers=1,
                retry_fallbacks=False,
            )
            client = Mock()
            client.complete.side_effect = [
                Completion("Illustrative direct response.", "mock", 10, 4, 1),
                Completion("{}", "mock", 10, 4, 1),
                Completion("{}", "mock", 10, 4, 1),
            ]
            self.assertEqual(execute(args, client), 0)
            self.assertEqual(execute(args, client), 0)
            self.assertEqual(client.complete.call_count, 3)
            rows = [json.loads(line) for line in (args.run_dir / "run-log.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [(row["version"], row["status"]) for row in rows],
                [("baseline", "success"), ("evidence", "fallback")],
            )


if __name__ == "__main__":
    unittest.main()
