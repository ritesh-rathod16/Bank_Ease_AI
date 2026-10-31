import pathlib
import sys
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app import auth, store
from backend.app.main import app
from backend.app.statements import StatementImportError, parse_statement_csv


SAMPLE = ROOT / "data" / "samples" / "statements" / "review_flags.csv"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "runtime")
    monkeypatch.setenv("APP_ENV", "test")
    old = dict(app.dependency_overrides)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "statement-owner", "name": "Test", "email": "test@example.test", "email_verified": True,
    }
    app.dependency_overrides[auth.require_csrf] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def test_sample_statement_normalizes_amounts_and_marks_review_candidates():
    parsed = parse_statement_csv(SAMPLE.read_bytes())
    analysis = parsed["statement_analysis"]

    assert parsed["transaction_count"] == 4
    assert parsed["currency"] == "INR"
    assert parsed["transactions"][1]["amount_minor"] == -425000
    assert parsed["transactions"][0]["source_values"]["credit"] == "125000.00"
    assert analysis["opening_balance_minor"] == 0
    assert analysis["closing_balance_minor"] == 11460000
    assert analysis["balance_check"] == "inconsistent"
    assert analysis["balance_issues"] == [{
        "row_number": 4, "expected_minor": 11650000,
        "observed_minor": 11660000, "difference_minor": 10000,
    }]
    assert analysis["duplicate_candidates"][0]["rows"] == [3, 4]


def test_signed_amount_csv_uses_explicit_direction_and_day_month_dates():
    parsed = parse_statement_csv(
        b"Date,Description,Amount,Type\n31/01/2026,Cafe,-4.50,DR\n01/02/2026,Refund,12.00,CR\n"
    )
    rows = parsed["transactions"]
    assert [row["date"] for row in rows] == ["2026-01-31", "2026-02-01"]
    assert [row["amount_minor"] for row in rows] == [-450, 1200]
    assert parsed["currency"] == "UNSPECIFIED"
    assert parsed["statement_analysis"]["balance_check"] == "unavailable"


def test_indian_lakh_grouping_is_normalized_exactly():
    parsed = parse_statement_csv(
        b'date,description,amount,type,currency\n2026-01-31,Transfer,"12,34,567.89",CR,INR\n'
    )
    assert parsed["transactions"][0]["amount_minor"] == 123456789


@pytest.mark.parametrize("content", [
    b"date,description,amount\n2026-01-01,Coffee,12\n",
    b"date,description,debit,credit\n2026-01-01,Coffee,-1,0\n",
    b"date,description,debit,credit\n2026-01-01,Coffee,1,2\n",
    b"date,description,debit,credit\n2026-01-01,Coffee,1.001,\n",
    b"date,description,debit,credit\n2026-01-01,Coffee,90071992547409.92,\n",
    b"not csv",
])
def test_unsupported_or_ambiguous_csv_is_rejected(content):
    with pytest.raises(StatementImportError):
        parse_statement_csv(content)


def test_csv_upload_and_owner_scoped_transaction_pagination(client):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("review_flags.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["doc_type"] == "bank_statement_csv"
    assert document["transaction_count"] == 4
    assert document["reconciliation_status"] == "inconsistent"

    summary = client.get(f"/api/v1/documents/{document['id']}").json()
    source = store.ROOT / "uploads" / f"{document['id']}.csv"
    assert source.read_bytes() == SAMPLE.read_bytes()
    assert summary["source_available"] is True and "source_file" not in summary
    preview = client.get(f"/api/v1/documents/{document['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/plain")
    assert preview.content == SAMPLE.read_bytes()
    assert summary["statement_analysis"]["balance_issues"][0]["row_number"] == 4
    assert "transactions" not in summary
    page = client.get(f"/api/v1/documents/{document['id']}/transactions?offset=1&limit=2")
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert [row["row_number"] for row in page.json()["transactions"]] == [3, 4]
    filtered = client.get(f"/api/v1/documents/{document['id']}/transactions?search=grocer&date_from=2026-01-02&date_to=2026-01-02")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert {row["description"] for row in filtered.json()["transactions"]} == {"Groceries"}
    assert client.get(f"/api/v1/documents/{document['id']}/transactions?date_from=2026-01-03&date_to=2026-01-01").status_code == 422
    assert "statement summary" in client.get(f"/documents/{document['id']}/report").text

    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "different-owner", "name": "Other", "email": "other@example.test", "email_verified": True,
    }
    assert client.get(f"/api/v1/documents/{document['id']}/transactions").status_code == 404
    assert client.get(f"/api/v1/documents/{document['id']}/preview").status_code == 404


def test_csv_type_mismatch_returns_clear_conflict(client):
    response = client.post(
        "/documents/analyze",
        files={"file": ("loan.csv", b"date,description,amount,type\n2026-01-01,Cafe,12,DR\n", "text/csv")},
    )
    assert response.status_code == 422
    assert "conflicts with a debit direction" in response.json()["detail"]


def test_statement_transaction_correction_preserves_source_and_tracks_revisions(client):
    uploaded = client.post(
        "/api/v1/documents",
        files={"file": ("review_flags.csv", SAMPLE.read_bytes(), "text/csv")},
    ).json()
    document_id = uploaded["id"]
    corrected = client.patch(
        f"/api/v1/documents/{document_id}/transactions/3",
        json={
            "date": "2026-01-04", "description": "Groceries - corrected", "amount_minor": -426000,
            "expected_revision": 0, "reason": "Checked against the transaction receipt.",
        },
    )
    assert corrected.status_code == 200, corrected.text
    row = corrected.json()
    assert row["date"] == "2026-01-02"
    assert row["description"] == "Groceries"
    assert row["amount_minor"] == -425000
    assert row["source_values"]["debit"] == "4250.00"
    assert row["effective"]["date"] == "2026-01-04"
    assert row["effective"]["description"] == "Groceries - corrected"
    assert row["effective"]["amount_minor"] == -426000
    assert row["effective"]["debit_minor"] == 426000
    assert row["correction_revision"] == 1
    assert row["correction_history"][0]["reason"] == "Checked against the transaction receipt."

    stale = client.patch(
        f"/api/v1/documents/{document_id}/transactions/3",
        json={"description": "stale update", "expected_revision": 0},
    )
    assert stale.status_code == 409
    stored = client.get(f"/api/v1/documents/{document_id}/transactions?offset=1&limit=1").json()
    assert stored["transactions"][0]["effective"]["amount_minor"] == -426000
    effective_filter = client.get(
        f"/api/v1/documents/{document_id}/transactions?search=corrected&date_from=2026-01-04&date_to=2026-01-04"
    ).json()
    assert effective_filter["total"] == 1
    assert effective_filter["transactions"][0]["row_number"] == 3

    reverted = client.patch(
        f"/api/v1/documents/{document_id}/transactions/3",
        json={"date": None, "description": None, "amount_minor": None,
              "expected_revision": 1, "reason": "Receipt review withdrawn."},
    )
    assert reverted.status_code == 200
    row = reverted.json()
    assert row["effective"]["date"] == row["date"] == "2026-01-02"
    assert row["effective"]["description"] == row["description"] == "Groceries"
    assert row["effective"]["amount_minor"] == row["amount_minor"] == -425000
    assert row["correction_revision"] == 2
    assert row["correction_history"][-1]["action"] == "revert"
    assert row["user_correction"] is None
    exported = client.get("/api/v1/auth/export")
    assert exported.status_code == 200
    export_row = next(item for item in exported.json()["transactions"]
                      if item["document_id"] == document_id and item["row_number"] == 3)
    assert export_row["correction_revision"] == 2
    assert len(export_row["correction_history"]) == 2
    exported_document = next(item for item in exported.json()["documents"] if item["id"] == document_id)
    assert "transactions" not in exported_document
    archive = client.get("/api/v1/auth/export/archive")
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as bundle:
        archive_account = json.loads(bundle.read("account.json"))
        archive_row = next(item for item in archive_account["transactions"]
                           if item["document_id"] == document_id and item["row_number"] == 3)
        assert len(archive_row["correction_history"]) == 2


def test_statement_transaction_correction_validates_fields_and_owner(client):
    uploaded = client.post(
        "/api/v1/documents",
        files={"file": ("review_flags.csv", SAMPLE.read_bytes(), "text/csv")},
    ).json()
    document_id = uploaded["id"]
    base = f"/api/v1/documents/{document_id}/transactions/2"
    assert client.patch(base, json={"expected_revision": 0}).status_code == 422
    assert client.patch(base, json={"date": "01/02/2026", "expected_revision": 0}).status_code == 422
    assert client.patch(base, json={"amount_minor": 0, "expected_revision": 0}).status_code == 422
    assert client.patch(f"/api/v1/documents/{document_id}/transactions/99",
                        json={"description": "missing", "expected_revision": 0}).status_code == 404

    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "other-statement-owner", "name": "Other", "email": "other@example.test", "email_verified": True,
    }
    assert client.patch(base, json={"description": "unauthorized", "expected_revision": 0}).status_code == 404
