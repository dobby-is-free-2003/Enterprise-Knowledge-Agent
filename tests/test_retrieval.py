import json

from eka.retrieval import subqueries


def _files(res):
    return {p.chunk.file_id for p in res.passages}


def test_restricted_documents_are_invisible_to_unauthorised_users(deps, U):
    q = "salary band Staff Engineer"
    assert "hr-salary-bands" not in _files(deps.retriever.search(U("priya.sharma"), q))
    assert "hr-salary-bands" in _files(deps.retriever.search(U("rahul.verma"), q))


def test_user_level_acl(deps, U):
    q = "Priya promotion level L5 May review"
    assert "hr-priya-one-on-one" in _files(deps.retriever.search(U("priya.sharma"), q))
    assert "hr-priya-one-on-one" not in _files(deps.retriever.search(U("anita.desai"), q))
    assert "hr-priya-one-on-one" in _files(deps.retriever.search(U("rahul.verma"), q))  # via group:hr


def test_second_permission_check_catches_revocation_between_syncs(deps, U):
    """Index still has the old ACL, but the source of truth no longer grants access: must be blocked."""
    user = U("anita.desai")
    q = "discount approval policy sales director"
    before = deps.retriever.search(user, q)
    assert "sales-discount-policy" in _files(before)
    manifest = deps.settings.corpus_dir / "manifest.json"
    data = json.loads(manifest.read_text())
    data["files"]["sales-discount-policy"]["allowed_principals"] = ["group:exec"]
    manifest.write_text(json.dumps(data))
    after = deps.retriever.search(user, q)
    assert "sales-discount-policy" not in _files(after)
    assert after.blocked_by_recheck >= 1


def test_relevance_gate_refuses_unknown_topics(deps, U):
    res = deps.retriever.search(U("priya.sharma"), "Who won the cricket world cup?")
    assert not deps.retriever.is_relevant(res)


def test_subquery_splitting():
    assert subqueries("What is the hotel cap and the meal limit for business travel?") == [
        "What is the hotel cap", "the meal limit for business travel"]
    assert subqueries("How many days of annual leave do I get?") == []
    assert subqueries("pet insurance and parental leave") == ["pet insurance", "parental leave"]
    assert subqueries("pet insurance and sabbatical") == []   # a one-word part is too thin to check alone


def test_compound_question_needs_every_part_covered(deps, U):
    user = U("priya.sharma")
    ok = deps.retriever.search(user, "How long are database backups retained and how soon must a lost device be reported?")
    assert deps.retriever.is_relevant(ok) and {"sec-data-retention", "sec-device-policy"} <= _files(ok)
    half = deps.retriever.search(user, "How long are database backups retained and who won the cricket world cup?")
    assert not deps.retriever.is_relevant(half)
