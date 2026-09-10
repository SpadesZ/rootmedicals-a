# File Path: ebm-rag/tests/test_citation_ref_indirection.py
# Description: The EBM prompt shows local refs (C1..CN) instead of real chunk ids,
#              because real ids end in a running number the model extrapolates from —
#              it cited a neighbouring chunk it was never shown, and the all-or-nothing
#              citation validator then discarded otherwise valid answers.
#              These tests pin both halves: the prompt must not leak sequential ids,
#              and unresolvable refs must still fail closed.
# ----------------------------------------------------------------------------------------------------

import pathlib
import sys
import unittest

EBM_RAG_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.core4_ragging.pipeline import _resolve_source_refs, _source_validation_errors
from rag_core.core4_ragging.prompts import MAX_PROMPT_CHUNKS, build_ebm_prompt

CHUNKS = [
    {"chunk_id": "RM_ARIA_ORAL_OCULAR_2026:chunk:000003", "text": "a", "pmid": "1", "doi": "d1"},
    {"chunk_id": "RM_ARIA_ORAL_OCULAR_2026:chunk:000005", "text": "b", "pmid": "2", "doi": "d2"},
]


def _hits(*chunk_ids):
    return {"rag_comments": [{
        "comment": "clinical statement",
        "sources": [{"chunk_id": cid} for cid in chunk_ids],
    }]}


class CitationRefIndirectionTests(unittest.TestCase):
    def test_prompt_hides_sequential_chunk_ids(self):
        prompt = build_ebm_prompt("query", {}, CHUNKS)[0]["content"]
        self.assertNotIn("chunk:000003", prompt)
        self.assertNotIn("chunk:000005", prompt)
        self.assertIn("Ref: C1", prompt)
        self.assertIn("Ref: C2", prompt)

    def test_prompt_states_the_valid_ref_set(self):
        prompt = build_ebm_prompt("query", {}, CHUNKS)[0]["content"]
        self.assertIn("Available refs", prompt)
        self.assertIn("exactly 2", prompt)

    def test_prompt_window_is_bounded(self):
        many = [dict(CHUNKS[0], chunk_id="p:chunk:%06d" % i) for i in range(MAX_PROMPT_CHUNKS + 5)]
        prompt = build_ebm_prompt("query", {}, many)[0]["content"]
        self.assertIn("Ref: C%d" % MAX_PROMPT_CHUNKS, prompt)
        self.assertNotIn("Ref: C%d" % (MAX_PROMPT_CHUNKS + 1), prompt)

    def test_refs_resolve_to_real_chunk_ids(self):
        hits = _hits("C1", "c2")
        _resolve_source_refs(CHUNKS, hits)
        resolved = [s["chunk_id"] for s in hits["rag_comments"][0]["sources"]]
        self.assertEqual(resolved, [CHUNKS[0]["chunk_id"], CHUNKS[1]["chunk_id"]])
        self.assertEqual(_source_validation_errors(CHUNKS, hits), [])

    def test_real_chunk_id_still_accepted(self):
        hits = _hits(CHUNKS[1]["chunk_id"])
        _resolve_source_refs(CHUNKS, hits)
        self.assertEqual(_source_validation_errors(CHUNKS, hits), [])

    def test_unknown_ref_still_fails_closed(self):
        hits = _hits("C9")
        _resolve_source_refs(CHUNKS, hits)
        self.assertEqual(hits["rag_comments"][0]["sources"][0]["chunk_id"], "C9")
        self.assertEqual(len(_source_validation_errors(CHUNKS, hits)), 1)

    def test_invented_neighbour_chunk_id_still_fails_closed(self):
        # The exact regression: chunk:000004 sits between two retrieved chunks
        # but was never retrieved, so it must not become citable.
        hits = _hits("RM_ARIA_ORAL_OCULAR_2026:chunk:000004")
        _resolve_source_refs(CHUNKS, hits)
        self.assertEqual(len(_source_validation_errors(CHUNKS, hits)), 1)

    def test_resolution_does_not_invent_sources_when_empty(self):
        hits = {"rag_comments": [{"comment": "x", "sources": []}]}
        _resolve_source_refs(CHUNKS, hits)
        self.assertTrue(_source_validation_errors(CHUNKS, hits))


if __name__ == "__main__":
    unittest.main()
