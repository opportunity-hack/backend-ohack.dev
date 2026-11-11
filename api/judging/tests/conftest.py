"""Keep the judge views hermetic: by default the caller-identity lookups
(Firestore + PropelAuth) find nothing, so every route behaves exactly as
before — the caller's login id is the judge id. Tests that exercise the real
resolver opt out with @pytest.mark.real_judge_identity."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_judge_identity: run the real judge identity resolver")


@pytest.fixture(autouse=True)
def _no_judge_identity_lookups(request, monkeypatch):
    if request.node.get_closest_marker("real_judge_identity"):
        return
    from api.judging import judge_identity
    monkeypatch.setattr(judge_identity, "_judge_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(judge_identity, "_all_event_judge_snapshots", lambda *_a, **_k: [])
