import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("GUARDIAN_DB_PATH", str(BACKEND_DIR / "guardian.db"))

DEMO_START = "2026-11-20T08:00:00"
APPROVAL_THRESHOLD_EUR = 300

# Escalation ladder thresholds
CHECKIN_MIN_RISK = 0.6
VISIT_MIN_CONFIDENCE = 0.8
TENANT_YES_BOOST = 0.15
SILENCE_ACCEPT_HOURS = 24
