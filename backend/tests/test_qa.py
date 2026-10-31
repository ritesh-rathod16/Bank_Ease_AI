from backend.app import qa


def test_exact_question_phrase_retrieves_insurance_waiting_period_passage():
    pages = [
        "Health Insurance Policy Schedule\n"
        "EXCLUSIONS\n"
        "Pre-existing conditions are excluded during the stated waiting period.\n"
        "WAITING PERIOD\n"
        "A waiting period applies to specified treatments.\n"
        "The schedule does not state the duration for all waiting periods."
    ]

    result = qa.answer("What is a waiting period?", pages)

    assert result["found"] is True
    assert result["sources"]
    assert any("waiting period" in source["text"].lower() for source in result["sources"])


def test_exclusion_question_retrieves_the_clause_not_only_the_section_heading():
    pages = [
        "Health Insurance Policy Schedule\n"
        "EXCLUSIONS\n"
        "Pre-existing conditions are excluded during the stated waiting period.\n"
        "WAITING PERIOD\n"
        "A waiting period applies to specified treatments."
    ]

    result = qa.answer("What exclusions does my policy contain?", pages)

    assert result["found"] is True
    assert any("pre-existing conditions are excluded" in source["text"].lower()
               for source in result["sources"])


def test_multi_document_retrieval_accepts_exact_phrase_and_preserves_document_citation():
    result = qa.answer_documents("What is the interest rate?", [{
        "id": "loan-id", "filename": "loan.pdf", "pages": [
            "PERSONAL LOAN AGREEMENT\nRate of Interest: 12.00% per annum (fixed)."
        ],
    }])

    assert result["found"] is True
    assert result["sources"][0]["filename"] == "loan.pdf"
    assert result["sources"][0]["page"] == 1
    assert "12.00%" in result["sources"][0]["text"]


def test_unrelated_question_still_returns_no_document_evidence():
    result = qa.answer("What lender mailing address is stated?", [
        "PERSONAL LOAN AGREEMENT\nRate of Interest: 12.00% per annum (fixed)."
    ])

    assert result["found"] is False
    assert result["sources"] == []


def test_retrieval_score_threshold_is_configurable_but_exact_phrase_survives(monkeypatch):
    monkeypatch.setenv("AI_RETRIEVAL_MIN_SCORE", "1000")

    result = qa.answer("What is a waiting period?", [
        "WAITING PERIOD\nA waiting period applies to specified treatments."
    ])

    assert result["found"] is True
