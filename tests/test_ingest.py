import json

from eka.ingest import ingest


def _run(deps):
    return ingest(deps.source, deps.store, deps.embedder, deps.settings.state_db_path)


def test_second_run_is_a_no_op(deps):
    n = deps.store.count()
    stats = _run(deps)
    assert stats.unchanged == stats.seen and stats.new == stats.updated == stats.deleted == 0
    assert deps.store.count() == n


def test_acl_change_triggers_reindex_and_new_acl_is_enforced(deps, U):
    manifest = deps.settings.corpus_dir / "manifest.json"
    data = json.loads(manifest.read_text())
    data["files"]["hr-leave-policy"]["allowed_principals"] = ["group:hr"]
    manifest.write_text(json.dumps(data))
    stats = _run(deps)
    assert stats.updated == 1
    deps.retriever.refresh()
    q = "annual leave days carry over"
    assert not any(p.chunk.file_id == "hr-leave-policy" for p in deps.retriever.search(U("priya.sharma"), q).passages)
    assert any(p.chunk.file_id == "hr-leave-policy" for p in deps.retriever.search(U("rahul.verma"), q).passages)


def test_content_change_and_deletion(deps):
    p = deps.settings.corpus_dir / "hr-leave-policy.md"
    p.write_text(p.read_text() + "\n## Extra\nNew section about volunteering leave.\n")
    assert _run(deps).updated == 1
    manifest = deps.settings.corpus_dir / "manifest.json"
    data = json.loads(manifest.read_text())
    del data["files"]["hr-holidays-2026"]
    manifest.write_text(json.dumps(data))
    stats = _run(deps)
    assert stats.deleted == 1
    assert not any(c.file_id == "hr-holidays-2026" for c in deps.store.iter_chunks())
