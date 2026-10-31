import pathlib
import sys
import pymupdf

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app import auth, store
from backend.app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "runtime")
    monkeypatch.setenv("APP_ENV", "test")
    old = dict(app.dependency_overrides)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "compare-owner", "name": "Test", "email": "compare@example.test", "email_verified": True,
    }
    app.dependency_overrides[auth.require_csrf] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def upload_statement(client, filename, rows, currency="INR"):
    lines = ["date,description,debit,credit,running_balance,currency", *rows]
    response = client.post(
        "/api/v1/documents",
        files={"file": (filename, ("\n".join(lines) + "\n").encode(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def statement_pair(client, currency_a="INR", currency_b="INR"):
    a = upload_statement(client, "january-a.csv", [
        "2026-01-01,Salary,,1000.00,1000.00," + currency_a,
        "2026-01-02,Groceries,100.00,,900.00," + currency_a,
        "2026-01-03,Monthly fee,10.00,,890.00," + currency_a,
    ], currency_a)
    b = upload_statement(client, "january-b.csv", [
        "2026-01-01,Salary,,1000.00,1010.00," + currency_b,
        "2026-01-02, groceries ,100.00,,910.00," + currency_b,
        "2026-01-03,Bonus,,50.00,960.00," + currency_b,
    ], currency_b)
    return a, b


def test_statement_comparison_matches_exact_rows_and_cites_both_sides(client):
    a, b = statement_pair(client)
    response = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparable"] is True
    assert result["comparison_id"]
    assert result["summary"] == {
        "matched_transaction_count": 2,
        "only_in_a_count": 1,
        "only_in_b_count": 1,
        "running_balance_difference_count": 2,
        "difference_count": 4,
    }
    assert result["balance_comparison"]["opening_balance_minor"]["difference_minor"] == 1000
    fee = next(item for item in result["differences"] if item["kind"] == "only_in_a")
    assert fee["description"] == "Monthly fee"
    assert fee["a"]["row_number"] == 4
    assert fee["a"]["source_values"]["debit"] == "10.00"
    assert fee["b"] is None
    assert "not proof of error" in result["note"]


def test_currency_mismatch_abstains_from_amount_comparison(client):
    a, b = statement_pair(client, "INR", "USD")
    response = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b})
    assert response.status_code == 200
    result = response.json()
    assert result["comparable"] is False
    assert result["differences"] == []
    assert "same identified currency" in result["note"]


def test_statement_comparison_report_is_a_pdf(client):
    a, b = statement_pair(client)
    response = client.post("/api/v1/comparisons/statements/report", json={"document_id_a": a, "document_id_b": b})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-")
    with pymupdf.open(stream=response.content, filetype="pdf") as report:
        report_text = "\n".join(page.get_text() for page in report)
    assert "10.00 INR" in report_text
    assert "minor units" not in report_text


def test_statement_comparison_does_not_allow_cross_owner_document_ids(client):
    a, b = statement_pair(client)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "different-owner", "name": "Other", "email": "other@example.test", "email_verified": True,
    }
    response = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b})
    assert response.status_code == 404


def test_statement_comparison_rejects_the_same_document(client):
    a, _ = statement_pair(client)
    response = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": a})
    assert response.status_code == 422


def test_saved_statement_comparison_can_be_listed_and_reopened_from_snapshot(client):
    a, b = statement_pair(client)
    created = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b}).json()
    comparison_id = created["comparison_id"]
    listed = client.get("/api/v1/comparisons?kind=statement").json()
    assert len(listed["comparisons"]) == 1
    assert listed["comparisons"][0]["id"] == comparison_id
    assert "result" not in listed["comparisons"][0]
    reopened = client.get(f"/api/v1/comparisons/{comparison_id}")
    assert reopened.status_code == 200
    assert reopened.json()["result"]["summary"] == created["summary"]
    assert reopened.json()["request"]["document_id_a"] == a
    exported = client.get("/api/v1/auth/export").json()
    assert any(item["id"] == comparison_id for item in exported["comparisons"])


def test_saved_statement_report_uses_the_frozen_result_snapshot(client, monkeypatch):
    a, b = statement_pair(client)
    created = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b}).json()

    def should_not_recompute(*args, **kwargs):
        raise AssertionError("Saved report should use its stored snapshot")

    monkeypatch.setattr(store, "all_transactions", should_not_recompute)
    response = client.get(f"/api/v1/comparisons/{created['comparison_id']}/report")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    with pymupdf.open(stream=response.content, filetype="pdf") as report:
        text = "\n".join(page.get_text() for page in report)
    assert "Monthly fee" in text
    assert "CSV row 4" in text


def test_saved_comparison_is_owner_scoped_and_deletable(client):
    a, b = statement_pair(client)
    created = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b}).json()
    comparison_id = created["comparison_id"]
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "another-owner", "name": "Other", "email": "other@example.test", "email_verified": True,
    }
    assert client.get(f"/api/v1/comparisons/{comparison_id}").status_code == 404
    assert client.delete(f"/api/v1/comparisons/{comparison_id}").status_code == 404
    assert client.get("/api/v1/comparisons?kind=statement").json()["comparisons"] == []
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "compare-owner", "name": "Test", "email": "compare@example.test", "email_verified": True,
    }
    assert client.delete(f"/api/v1/comparisons/{comparison_id}").status_code == 204
    assert client.get(f"/api/v1/comparisons/{comparison_id}").status_code == 404


def test_saved_comparison_history_is_paginated_and_validates_filters(client):
    a, b = statement_pair(client)
    for _ in range(2):
        response = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b})
        assert response.status_code == 200
    first_page = client.get("/api/v1/comparisons?kind=statement&offset=0&limit=1")
    assert first_page.status_code == 200
    assert len(first_page.json()["comparisons"]) == 1
    assert first_page.json()["has_more"] is True
    second_page = client.get("/api/v1/comparisons?kind=statement&offset=1&limit=1").json()
    assert len(second_page["comparisons"]) == 1
    assert second_page["comparisons"][0]["id"] != first_page.json()["comparisons"][0]["id"]
    assert client.get("/api/v1/comparisons?kind=unknown").status_code == 422
    assert client.get("/api/v1/comparisons?limit=101").status_code == 422


def test_document_deletion_removes_referencing_comparison(client):
    a, b = statement_pair(client)
    created = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b}).json()
    assert client.delete(f"/api/v1/documents/{a}").status_code == 204
    assert client.get(f"/api/v1/comparisons/{created['comparison_id']}").status_code == 404
    assert client.get("/api/v1/comparisons?kind=statement").json()["comparisons"] == []


def test_loan_term_correction_endpoint_rejects_statement_rows(client):
    a, _ = statement_pair(client)
    response = client.patch(f"/api/v1/documents/{a}/fields/loan_amount", json={"value": "1000"})
    assert response.status_code == 409


def test_statement_comparison_uses_effective_transaction_corrections(client):
    a, b = statement_pair(client)
    correction = client.patch(
        f"/api/v1/documents/{b}/transactions/4",
        json={
            "description": "Monthly fee", "amount_minor": -1000, "balance_minor": 89000,
            "expected_revision": 0,
        },
    )
    assert correction.status_code == 200, correction.text
    # The original statement row remains a bonus; a new comparison uses the corrected effective values.
    assert correction.json()["description"] == "Bonus"
    result = client.post("/api/v1/comparisons/statements", json={"document_id_a": a, "document_id_b": b})
    assert result.status_code == 200, result.text
    assert result.json()["summary"]["matched_transaction_count"] == 3
    assert result.json()["summary"]["only_in_a_count"] == 0
    assert result.json()["summary"]["only_in_b_count"] == 0
    assert result.json()["summary"]["running_balance_difference_count"] == 2
