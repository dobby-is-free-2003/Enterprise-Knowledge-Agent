from eka.chunking import chunk_document
from eka.models import Document


def _doc(text):
    return Document(file_id="f1", title="T", text=text, owner="o", allowed_principals=["group:all-staff"])


def test_heading_path_is_kept():
    chunks = chunk_document(_doc("# A\n## B\nBody one.\n\n## C\nBody two."))
    assert [c.heading_path for c in chunks] == ["A > B", "A > C"]
    assert chunks[0].chunk_id == "f1#0" and chunks[1].chunk_id == "f1#1"


def test_acl_copied_onto_every_chunk():
    chunks = chunk_document(_doc("# A\nx\n\n# B\ny"))
    assert all(c.allowed_principals == ["group:all-staff"] for c in chunks)


def test_long_paragraph_is_split_with_overlap():
    words = [f"w{i}" for i in range(1000)]
    chunks = chunk_document(_doc("# A\n" + " ".join(words)), max_words=300, overlap_words=50)
    assert len(chunks) >= 4
    assert all(len(c.text.split()) <= 300 for c in chunks)
    first_tail = chunks[0].text.split()[-50:]
    assert chunks[1].text.split()[:50] == first_tail


def test_hash_is_stable_and_changes_with_text():
    a = chunk_document(_doc("# A\nsame text"))[0].content_hash
    b = chunk_document(_doc("# A\nsame text"))[0].content_hash
    c = chunk_document(_doc("# A\ndifferent"))[0].content_hash
    assert a == b != c


def test_empty_document_has_no_chunks():
    assert chunk_document(_doc("")) == []
