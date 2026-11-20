"""The post-mine integrity check runs in a shared phase of the hub lock.

``_validate_palace_fts5_after_mine`` closes the shared Chroma client, then runs
``PRAGMA quick_check`` through its own read-only connection. On a multi-GB
palace that check runs for minutes; a hub serving the mine must let readers run
meanwhile (``mine_shared_phase``) instead of making every request wait.
"""

import contextlib

import mempalace.palace as palace
import mempalace.repair as repair


@contextlib.contextmanager
def _fake_phase(log):
    log.append("enter")
    try:
        yield True
    finally:
        log.append("exit")


def _patch(monkeypatch, log, errors):
    monkeypatch.setattr(palace, "resolve_backend_name", lambda _p: "chroma")
    monkeypatch.setattr(repair, "_close_chroma_handles", lambda *_a, **_k: log.append("close"))

    def check(_path):
        log.append("check")
        return list(errors)

    monkeypatch.setattr(repair, "sqlite_integrity_errors", check)
    monkeypatch.setattr(
        repair,
        "maybe_autoheal_fts5_index",
        lambda _p, errs, progress=None: log.append("heal") or [],
    )


def test_quick_check_runs_inside_the_shared_phase(monkeypatch, tmp_path):
    log = []
    _patch(monkeypatch, log, errors=[])
    with palace.mine_share_hook(lambda: _fake_phase(log)):
        palace._validate_palace_fts5_after_mine(str(tmp_path))
    assert log == ["close", "enter", "check", "exit"]


def test_without_a_hub_hook_the_check_runs_unchanged(monkeypatch, tmp_path):
    log = []
    _patch(monkeypatch, log, errors=[])
    palace._validate_palace_fts5_after_mine(str(tmp_path))
    assert log == ["close", "check"]


def test_handles_are_closed_again_before_a_heal_after_a_shared_phase(monkeypatch, tmp_path):
    log = []
    _patch(monkeypatch, log, errors=["fts5 malformed"])
    with palace.mine_share_hook(lambda: _fake_phase(log)):
        palace._validate_palace_fts5_after_mine(str(tmp_path))
    assert log == ["close", "enter", "check", "exit", "close", "heal"]


def test_no_second_close_before_a_heal_without_a_shared_phase(monkeypatch, tmp_path):
    log = []
    _patch(monkeypatch, log, errors=["fts5 malformed"])
    palace._validate_palace_fts5_after_mine(str(tmp_path))
    assert log == ["close", "check", "heal"]
