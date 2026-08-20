"""The CLI surface. Exit codes are the product: nobody reads a green run."""

from __future__ import annotations

import json

import pytest

from scraper_agent.monitor import cli as cli_module
from scraper_agent.monitor.config import load_watchlist
from scraper_agent.monitor.detect import ALERT, OK, WARN, Finding, Verdict
from scraper_agent.monitor.history import History, Observation
from scraper_agent.monitor.runner import MonitorReport, PageReport

URL = "https://shop.com/collections/all"


def _watchlist_file(tmp_path, **extra):
    path = tmp_path / "w.json"
    data = {"pages": [{"url": URL}], "state_path": str(tmp_path / "h.jsonl")}
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _report(severity=OK, findings=None, verdict=None):
    return MonitorReport(
        pages=[
            PageReport(
                page_url=URL,
                canary=True,
                severity=severity,
                findings=findings or [],
                observation=Observation(page_url=URL),
            )
        ],
        verdict=verdict or Verdict("none", "high", "No degradation across 1 judged page(s)."),
    )


def test_init_emits_a_loadable_watchlist(capsys, tmp_path):
    assert cli_module.main(["init"]) == 0
    path = tmp_path / "w.json"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert len(load_watchlist(path).pages) == 3


def test_no_command_prints_help(capsys):
    assert cli_module.main([]) == 2
    assert "scrape-agent-monitor" in capsys.readouterr().out


def test_a_broken_watchlist_exits_two_with_a_message(tmp_path, capsys):
    path = tmp_path / "w.json"
    path.write_text("{}", encoding="utf-8")
    assert cli_module.main(["run", "--config", str(path)]) == 2
    assert "pages" in capsys.readouterr().err


def test_a_clean_run_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "run_monitor", lambda *a, **k: _report(OK))
    assert cli_module.main(["run", "--config", str(_watchlist_file(tmp_path)), "-q"]) == 0
    assert "verdict: clean" in capsys.readouterr().out


@pytest.mark.parametrize(
    "severity, fail_on, expected",
    [(WARN, "alert", 0), (WARN, "warn", 1), (ALERT, "alert", 2)],
)
def test_fail_on_controls_the_exit_code(tmp_path, monkeypatch, severity, fail_on, expected):
    monkeypatch.setattr(cli_module, "run_monitor", lambda *a, **k: _report(severity))
    code = cli_module.main(
        ["run", "--config", str(_watchlist_file(tmp_path)), "-q", "--fail-on", fail_on]
    )
    assert code == expected


def test_findings_are_printed_under_their_page(tmp_path, monkeypatch, capsys):
    findings = [Finding("record_count", ALERT, "records fell 87% (baseline 23, now 3)")]
    monkeypatch.setattr(cli_module, "run_monitor", lambda *a, **k: _report(ALERT, findings))

    cli_module.main(["run", "--config", str(_watchlist_file(tmp_path)), "-q"])

    out = capsys.readouterr().out
    assert "FAIL" in out and "records fell 87%" in out


def test_json_report_is_written_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "run_monitor", lambda *a, **k: _report(ALERT))
    out = tmp_path / "nested" / "report.json"

    cli_module.main(
        ["run", "--config", str(_watchlist_file(tmp_path)), "-q", "--json", str(out)]
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["severity"] == ALERT
    assert payload["pages"][0]["page_url"] == URL


def test_status_without_history_says_so(tmp_path, capsys):
    assert cli_module.main(["status", "--config", str(_watchlist_file(tmp_path))]) == 0
    assert "No history yet" in capsys.readouterr().out


def test_status_lists_recent_runs(tmp_path, capsys):
    state = tmp_path / "h.jsonl"
    history = History(state)
    history.append(
        Observation(
            page_url=URL,
            signals={"record_count": 23},
            metrics={"recall": 0.88, "precision": 1.0},
        )
    )
    history.append(Observation(page_url=URL, ok=False, error="FetchError: 403"))

    cli_module.main(["status", "--config", str(_watchlist_file(tmp_path)), "--state", str(state)])

    out = capsys.readouterr().out
    assert "23 records" in out and "recall 88%" in out
    assert "403" in out


def test_state_flag_overrides_the_watchlist_path(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(watchlist, history, settings, **kwargs):
        seen["path"] = str(history.path)
        return _report(OK)

    monkeypatch.setattr(cli_module, "run_monitor", fake_run)
    override = tmp_path / "elsewhere.jsonl"

    cli_module.main(
        ["run", "--config", str(_watchlist_file(tmp_path)), "-q", "--state", str(override)]
    )

    assert seen["path"] == str(override)
