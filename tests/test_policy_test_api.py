"""Tests for policy test API endpoints."""
import pytest


def test_run_policy_test_allows_matching_command(client):
    """Test that a valid policy allows a matching command."""
    response = client.post("/api/admin/policy-test", json={
        "rego_policy": 'allow { input.argv[0] == "kubectl" }',
        "test_command": "kubectl get pods"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is True
    assert data["error"] is None
    assert "test_id" in data
    assert "explanation" in data


def test_run_policy_test_denies_non_matching_command(client):
    """Test that a valid policy denies a non-matching command."""
    response = client.post("/api/admin/policy-test", json={
        "rego_policy": 'allow { input.argv[0] == "kubectl" }',
        "test_command": "gh pr list"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is False
    assert data["error"] is None
    assert "test_id" in data


def test_run_policy_test_invalid_rego_syntax(client):
    """Test that invalid Rego syntax returns a detailed error."""
    response = client.post("/api/admin/policy-test", json={
        "rego_policy": 'allow { broken syntax',
        "test_command": "kubectl get pods"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is False
    assert data["error"] is not None
    assert "syntax" in data["error"].lower() or "parse" in data["error"].lower()


def test_run_policy_test_empty_command(client):
    """Test that empty command returns 422."""
    response = client.post("/api/admin/policy-test", json={
        "rego_policy": 'allow { true }',
        "test_command": ""
    })
    
    assert response.status_code == 422


def test_run_policy_test_stores_test_case(client):
    """Test that running a test stores it in the repository."""
    # Run test
    response = client.post("/api/admin/policy-test", json={
        "rego_policy": 'allow { input.argv[0] == "kubectl" }',
        "test_command": "kubectl get pods"
    })
    assert response.status_code == 200
    test_id = response.json()["test_id"]
    
    # List test cases
    response = client.get("/api/admin/policy-test")
    assert response.status_code == 200
    tests = response.json()
    assert len(tests) >= 1
    assert any(t["test_id"] == test_id for t in tests)


def test_list_policy_tests(client):
    """Test listing policy test cases."""
    # Create a few test cases
    for i in range(3):
        response = client.post("/api/admin/policy-test", json={
            "rego_policy": f'allow {{ input.argv[0] == "cmd{i}" }}',
            "test_command": f"cmd{i} arg"
        })
        assert response.status_code == 200
    
    # List test cases
    response = client.get("/api/admin/policy-test")
    assert response.status_code == 200
    tests = response.json()
    assert len(tests) >= 3


def test_delete_policy_test(client):
    """Test deleting a policy test case."""
    # Create a test case
    response = client.post("/api/admin/policy-test", json={
        "rego_policy": 'allow { true }',
        "test_command": "test command"
    })
    assert response.status_code == 200
    test_id = response.json()["test_id"]
    
    # Delete it
    response = client.delete(f"/api/admin/policy-test/{test_id}")
    assert response.status_code == 204
    
    # Verify it's gone
    response = client.get("/api/admin/policy-test")
    tests = response.json()
    assert not any(t["test_id"] == test_id for t in tests)


def test_delete_policy_test_not_found(client):
    """Test deleting a non-existent test case."""
    response = client.delete("/api/admin/policy-test/nonexistent")
    assert response.status_code == 404
