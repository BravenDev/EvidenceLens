"""Run the sample retrieval checks in temporary storage."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ROOT, Settings  # noqa: E402
from backend.ollama import Ollama  # noqa: E402
from backend.research import Research, SearchRequest  # noqa: E402
from backend.storage import Store  # noqa: E402


def main() -> None:
    cases = json.loads((ROOT / "examples" / "evaluation.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="evidencelens-eval-") as temp:
        settings = Settings(data_dir=Path(temp))
        store = Store(settings)
        store.initialize()
        research = Research(settings, store, Ollama(settings))
        for path in sorted((ROOT / "examples").glob("*.md")):
            research.upload(path.name, path.read_bytes())
        hit = 0
        reciprocal_ranks = []
        for case in cases:
            found = research.search(SearchRequest(query=case["query"], limit=5))["sources"]
            filenames = [item["filename"] for item in found]
            expected = case["expected_document"]
            if expected:
                rank = filenames.index(expected) + 1 if expected in filenames else 0
                passed = bool(rank)
                reciprocal_ranks.append(1 / rank if rank else 0)
            else:
                passed = not found
            hit += passed
            print(f"{'PASS' if passed else 'FAIL'}: {case['query']} | {filenames}")
        print(f"Cases passed: {hit}/{len(cases)}")
        print(f"Answerable-query MRR@5: {sum(reciprocal_ranks) / len(reciprocal_ranks):.3f}")
        print("Only three fictional documents: a smoke baseline, not a general quality benchmark.")
        if hit != len(cases):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
