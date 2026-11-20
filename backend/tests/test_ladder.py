from app.ladder import next_step


def test_low_risk_rechecks():
    assert next_step(0.4, {}, True) == "recheck"


def test_first_step_is_checkin():
    assert next_step(0.72, {}, True) == "tenant_checkin"


def test_yes_skips_boiler_to_visit():
    assert next_step(0.72, {"tenant": "yes"}, True) == "visit"


def test_yes_but_building_abnormal_goes_remote_boiler():
    assert next_step(0.72, {"tenant": "yes"}, False) == "remote_boiler"


def test_no_closes():
    assert next_step(0.72, {"tenant": "no"}, True) == "close_no_action"
