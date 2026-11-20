from typing import Literal

from app import config

Step = Literal["recheck", "tenant_checkin", "remote_boiler", "visit", "close_no_action"]


def next_step(risk: float, answers: dict, building_signals_normal: bool) -> Step:
    """Pick the next escalation rung for a case."""
    if risk < config.CHECKIN_MIN_RISK:
        return "recheck"
    tenant = answers.get("tenant")
    if tenant is None:
        return "tenant_checkin"
    if tenant == "no":
        return "close_no_action"
    confidence = risk + (config.TENANT_YES_BOOST if tenant == "yes" else 0.0)
    if confidence >= config.VISIT_MIN_CONFIDENCE and building_signals_normal:
        return "visit"  # building looks fine, so the fault is local: skip the boiler
    return "remote_boiler"
