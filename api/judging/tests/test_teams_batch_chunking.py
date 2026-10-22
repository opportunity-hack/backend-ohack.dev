"""get_teams_batch must chunk `__name__ in [...]` by 30 — Firestore's cap —
or a judge/admin read over more teams than that raises and returns []."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import patch

import services.teams_service as ts


class _Doc:
    def __init__(self, doc_id):
        self.id = doc_id

    def to_dict(self):
        return {"name": f"Team {self.id}"}


class _Query:
    def __init__(self, refs, calls):
        self._refs, self._calls = refs, calls

    def stream(self):
        self._calls.append(len(self._refs))
        return [_Doc(r) for r in self._refs]


class _Collection:
    def __init__(self, calls):
        self._calls = calls

    def document(self, doc_id):
        return doc_id

    def where(self, field, op, refs):
        assert field == "__name__" and op == "in"
        assert len(refs) <= 30
        return _Query(refs, self._calls)


class _Db:
    def __init__(self, calls):
        self._calls = calls

    def collection(self, name):
        assert name == "teams"
        return _Collection(self._calls)


def test_get_teams_batch_chunks_by_30_and_dedupes():
    calls = []
    ids = [f"t{i}" for i in range(65)] + ["t0", "t1"]  # dupes collapse
    with patch.object(ts, "get_db", return_value=_Db(calls)), \
            patch.object(ts, "doc_to_json", side_effect=lambda docid, doc: {"id": docid, **doc.to_dict()}):
        results = ts.get_teams_batch({"team_ids": ids})
    assert calls == [30, 30, 5]
    assert len(results) == 65
    assert results[0]["name"] == "Team t0"


def test_get_teams_batch_empty_ids():
    assert ts.get_teams_batch({"team_ids": []}) == []
