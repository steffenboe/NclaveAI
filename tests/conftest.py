import os
from pathlib import Path


def pytest_configure(config):
    """Set required env vars before any module is imported."""
    rego = Path(__file__).parent.parent / "policies" / "executor.rego"
    os.environ.setdefault("POLICY_PATH", str(rego))
