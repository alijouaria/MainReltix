"""Canned English copy used when the LLM is unavailable (no key, offline, error)."""

TEXT = {
    "alert_explanation": (
        "Unit {unit_id} is using noticeably less heat than comparable flats in the building "
        "for several days. {evidence} This usually points to a radiator or valve fault."
    ),
    "tenant_checkin": (
        "Hi {tenant_name}, our system noticed your flat may be heating less than usual. "
        "Is the bedroom radiator cold even on setting 5?"
    ),
    "landlord_approval": (
        "Hello {owner_name}, we recommend a heating repair in unit {unit_id} by {craftsman_name}. "
        "Estimated cost: EUR {amount_eur}. Please approve or decline."
    ),
    "tenant_notice": (
        "Hi {tenant_name}, a heating technician from {craftsman_name} will visit on {slot}. "
        "Please make sure someone can let them in."
    ),
    "call_script": (
        "Hello, this is Guardian calling on behalf of reltix property management about a heating "
        "fault at {address}. {evidence} Could you send a technician?"
    ),
    "case_summary": (
        "Case for unit {unit_id}: {evidence} Status: {status}."
    ),
}
