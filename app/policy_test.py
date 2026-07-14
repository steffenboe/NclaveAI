from __future__ import annotations

import json
import logging
import shlex
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from regopy import Interpreter

from app.models import PolicyTestCase, PolicyTestResult

logger = logging.getLogger(__name__)


class PolicyTestRepository:
    """File-based repository for policy test cases."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._test_cases: list[PolicyTestCase] = []
            return
        try:
            data = json.loads(self._path.read_text())
            self._test_cases = [PolicyTestCase.model_validate(tc) for tc in data]
        except json.JSONDecodeError as exc:
            logger.warning(f"policy_test.json is not valid JSON: {exc}")
            self._test_cases = []
        except Exception as exc:
            logger.warning(f"Could not load policy_test.json: {exc}")
            self._test_cases = []

    def _save(self) -> None:
        self._path.write_text(
            json.dumps([tc.model_dump(mode="json") for tc in self._test_cases], indent=2)
        )

    def list_for_user(self, user_id: str) -> list[PolicyTestCase]:
        """List all test cases for a specific user."""
        with self._lock:
            return [tc for tc in self._test_cases if tc.user_id == user_id]

    def create(self, user_id: str, rego_policy: str, test_command: str) -> PolicyTestCase:
        """Create a new policy test case."""
        test_case = PolicyTestCase(
            test_id=str(uuid.uuid4()),
            user_id=user_id,
            rego_policy=rego_policy,
            test_command=test_command,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._test_cases.append(test_case)
            self._save()
        return test_case

    def delete(self, test_id: str, user_id: str) -> bool:
        """Delete a test case. Returns True if deleted, False if not found."""
        with self._lock:
            for i, tc in enumerate(self._test_cases):
                if tc.test_id == test_id and tc.user_id == user_id:
                    del self._test_cases[i]
                    self._save()
                    return True
            return False


def parse_command_string(command_str: str) -> list[str]:
    """Parse a command string into argv array using shell-like parsing."""
    try:
        return shlex.split(command_str)
    except ValueError as e:
        raise ValueError(f"Invalid command string: {e}")


def evaluate_policy_test(rego_policy: str, command_str: str) -> PolicyTestResult:
    """
    Evaluate a Rego policy against a command string.
    
    Args:
        rego_policy: Rego policy code (rule bodies without package line)
        command_str: Command string to test (e.g., "kubectl get pods")
    
    Returns:
        PolicyTestResult with allowed status, explanation, and optional error
    """
    try:
        # Parse command string into argv
        argv = parse_command_string(command_str)
        
        # Create interpreter and add policy
        interp = Interpreter()
        full_policy = f"package ops.agent\n{rego_policy}"
        interp.add_module("policy_test", full_policy)
        
        # Set input
        input_json = json.dumps({"argv": argv})
        interp.set_input_term(input_json)
        
        # Query the policy
        result = interp.query("data.ops.agent.allow")
        
        # Parse result
        result_str = str(result)
        allowed = result.ok() and '"expressions":[true]' in result_str
        
        # Build explanation
        explanation = {
            "argv": argv,
            "policy_result": result_str,
            "query": "data.ops.agent.allow",
        }
        
        return PolicyTestResult(
            allowed=allowed,
            explanation=explanation,
            error=None,
        )
        
    except Exception as e:
        error_msg = str(e)
        # Normalize regopy parse/compile errors to include "syntax" for clarity
        if error_msg.startswith("(rego-error") or "rego-error" in error_msg:
            error_msg = f"Rego syntax error: {error_msg}"
        return PolicyTestResult(
            allowed=False,
            explanation={},
            error=error_msg,
        )
