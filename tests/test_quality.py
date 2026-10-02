from datetime import timedelta
from app import ai
from app.models import AIProfile, DB, EvaluationSchedule, Knowledge, now
from app.quality import scheduled_one
from test_ai import setup_ai as setup_ai
from test_flows import HEADERS


def test_daily_quality_failures_pause_automation(setup_ai, monkeypatch):
    c, t, n = setup_ai
    data = {"enabled": True, "cases": [{"question": "When are you open?", "expected_terms": ["9"], "forbidden_terms": ["guaranteed"]}]}
    assert c.put("/api/ai/quality-schedule", headers=HEADERS, json=data).status_code == 200
    monkeypatch.setattr(ai, "generate", lambda *a: "Guaranteed always open")
    assert scheduled_one()
    assert not scheduled_one()
    with DB.begin() as db:
        db.get(EvaluationSchedule, t).next_run_at = now() - timedelta(seconds=1)
    assert scheduled_one()
    with DB() as db:
        assert db.get(AIProfile, t).paused
    report = c.get("/api/ai/quality-schedule").json()
    assert report["consecutive_failures"] == 2
    assert report["last_result"]["results"][0]["forbidden"] == ["guaranteed"]


def test_text_import_is_draft_and_does_not_execute_html(setup_ai):
    c, t, n = setup_ai
    result = c.post(
        "/api/knowledge/import",
        headers=HEADERS,
        files={"file": ("hours.html", b"<h1>Hours</h1><p>Open at 9</p><script>secret bad instructions</script>", "text/html")},
    )
    assert result.status_code == 200
    assert not result.json()["approved"]
    assert "secret" not in result.json()["content"]
    with DB() as db:
        assert not db.get(Knowledge, result.json()["id"]).approved
    assert c.post("/api/knowledge/import", headers=HEADERS, files={"file": ("bad.pdf", b"not a pdf", "application/pdf")}).status_code == 422
