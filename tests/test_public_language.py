from pathlib import Path


def test_public_ui_avoids_internal_operations_jargon():
    public = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "app/static/index.html",
            "app/static/app.js",
            "app/static/identity-mobile.js",
            "app/static/payment-status.html",
        )
    ).lower()

    forbidden = (
        "twilio",
        "regulatory bundle",
        "approved bu bundle sid",
        "ad address sid",
        "provider evidence reference",
        "action id",
        "stripe price id",
        "regression check",
        "end-user type mismatch",
    )

    for phrase in forbidden:
        assert phrase not in public, f"Internal wording leaked into public UI: {phrase}"
