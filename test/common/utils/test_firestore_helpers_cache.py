"""doc_to_json cache semantics (hardening 3.1).

- DocumentReference inputs are cached (the cache saves a Firestore .get()).
- DocumentSnapshot inputs are converted directly (the read already happened),
  so a fresher snapshot is never shadowed by a stale cached value.
- Every dict/list return is a copy: callers may mutate freely.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import MagicMock, patch

import pytest
from firebase_admin import firestore

import common.utils.firestore_helpers as fh
from common.utils.firestore_helpers import doc_to_json, clear_all_caches


def _snapshot(docid, data):
    snap = MagicMock(spec=firestore.DocumentSnapshot)
    snap.id = docid
    snap.to_dict.side_effect = lambda: {k: (list(v) if isinstance(v, list) else v) for k, v in data.items()}
    return snap


def _reference(docid, data):
    ref = MagicMock(spec=firestore.DocumentReference)
    ref.id = docid
    ref.get.return_value = _snapshot(docid, data)
    return ref


@pytest.fixture(autouse=True)
def _clear():
    doc_to_json.cache_clear()
    yield
    doc_to_json.cache_clear()


def test_reference_results_are_distinct_copies():
    ref = _reference("t1", {"name": "Team", "users": ["u1", "u2"], "project_story": "long"})
    first = doc_to_json(docid="t1", doc=ref)
    second = doc_to_json(docid="t1", doc=ref)
    assert first == second
    assert first is not second
    first.pop("project_story")
    first["users"].append("u3")
    third = doc_to_json(docid="t1", doc=ref)
    assert third["project_story"] == "long"
    assert third["users"] == ["u1", "u2"]
    # The reference was only read once (cache hit on later calls).
    assert ref.get.call_count == 1


def test_fresh_snapshot_is_not_shadowed_by_cached_reference_value():
    doc_to_json(docid="t1", doc=_reference("t1", {"name": "old"}))
    result = doc_to_json(docid="t1", doc=_snapshot("t1", {"name": "new"}))
    assert result["name"] == "new"


def test_nested_document_reference_leaf_survives_copy_by_identity():
    leaf = MagicMock(spec=firestore.DocumentReference)
    leaf.id = "leaf"
    ref = _reference("t1", {"items": [{"ref": leaf}]})
    first = doc_to_json(docid="t1", doc=ref)
    second = doc_to_json(docid="t1", doc=ref)
    assert first["items"][0] is not second["items"][0]
    assert first["items"][0]["ref"] is leaf
    assert second["items"][0]["ref"] is leaf


def test_list_references_are_flattened_to_ids():
    leaf = MagicMock(spec=firestore.DocumentReference)
    leaf.id = "u9"
    result = doc_to_json(docid="t1", doc=_snapshot("t1", {"users": [leaf, "u2"]}))
    assert result["users"] == ["u9", "u2"]
    assert result["id"] == "t1"


def test_non_firestore_input_returned_unchanged():
    obj = {"already": "json"}
    assert doc_to_json(docid="x", doc=obj) is obj
    assert doc_to_json(docid=None, doc=obj) is None
    assert doc_to_json(docid="x", doc=None) is None


def test_cache_clear_still_works_and_clear_all_caches_calls_it():
    ref = _reference("t1", {"name": "a"})
    doc_to_json(docid="t1", doc=ref)
    doc_to_json.cache_clear()
    doc_to_json(docid="t1", doc=ref)
    assert ref.get.call_count == 2

    with patch.object(fh, "doc_to_json") as mock_d2j:
        clear_all_caches()
    mock_d2j.cache_clear.assert_called_once()


def test_hash_key_still_importable():
    from common.utils.firestore_helpers import hash_key  # noqa: F401
