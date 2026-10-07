from app.call_routing import customer_destination


def policy(**overrides):
    value = {
        "customer_routing": {
            "enabled": True,
            "owner_routes": [
                {"owner": "Martin", "destination": "+447700900011"},
            ],
            "known_customer_destination": "+447700900099",
            "open_promise_destination": "+447700900022",
            "risk_destination": "+447700900033",
            "risk_threshold": 70,
            "revenue_destination": "+447700900044",
            "revenue_threshold": 100000,
        }
    }
    value["customer_routing"].update(overrides)
    return value


def brief(**overrides):
    value = {
        "known": True,
        "owner": "",
        "open_promises": 0,
        "risk_score": 0,
        "revenue_signal": 0,
    }
    value.update(overrides)
    return value


def test_unknown_or_disabled_customer_never_routes_from_customer_brain():
    assert customer_destination(policy(), brief(known=False)) == ""
    assert customer_destination(policy(enabled=False), brief(owner="Martin")) == ""


def test_customer_brain_route_precedence_is_deterministic():
    cfg = policy()
    assert customer_destination(
        cfg,
        brief(
            owner=" martin ",
            open_promises=2,
            risk_score=95,
            revenue_signal=500000,
        ),
    ) == "+447700900011"

    assert customer_destination(
        cfg,
        brief(open_promises=2, risk_score=95, revenue_signal=500000),
    ) == "+447700900022"

    assert customer_destination(
        cfg,
        brief(risk_score=95, revenue_signal=500000),
    ) == "+447700900033"

    assert customer_destination(
        cfg,
        brief(revenue_signal=500000),
    ) == "+447700900044"

    assert customer_destination(cfg, brief()) == "+447700900099"


def test_missing_specialist_destination_falls_back_instead_of_dropping_route():
    assert customer_destination(
        policy(open_promise_destination=""),
        brief(open_promises=1),
    ) == "+447700900099"

    assert customer_destination(
        policy(risk_destination=""),
        brief(risk_score=95),
    ) == "+447700900099"

    assert customer_destination(
        policy(revenue_destination=""),
        brief(revenue_signal=500000),
    ) == "+447700900099"


def test_missing_higher_priority_destination_can_continue_to_next_configured_rule():
    assert customer_destination(
        policy(risk_destination=""),
        brief(risk_score=95, revenue_signal=500000),
    ) == "+447700900044"

    assert customer_destination(
        policy(open_promise_destination="", risk_destination=""),
        brief(open_promises=1, risk_score=95, revenue_signal=500000),
    ) == "+447700900044"
