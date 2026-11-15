"""Re-indexing uploads at startup: a restart must not lose anyone's documents.

Uploads live at `uploads/<owner>/<file>` and are indexed **per owner**. On boot
the app walks that directory and re-adds anything missing from the vector store,
so a restart does not silently empty a user's document knowledge.

That loop (api.py:559-584) was never executed by a test. Three things in it are
easy to get wrong, and each is a data problem rather than a crash:

* an unowned file at the top level (from before per-owner storage) must be
  **reported and skipped**, never indexed into someone's namespace;
* a file already in the vector store must be **skipped**, so a restart does not
  re-embed (and re-summarise, at cost) the whole library;
* one unreadable file must **not** stop the rest of the walk.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _boot(monkeypatch, tmp_path, uploads):
    """Boot the real app with a prepared uploads tree; return (api, client, capsys-ready)."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-for-reindex")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-reindex-only")
    monkeypatch.setenv("ENVIRONMENT", "development")

    import phoenix.otel as _photel
    monkeypatch.setattr(_photel, "register", lambda *a, **k: None, raising=False)

    root = tmp_path / "uploads"
    for rel, content in uploads.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    import api
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "UPLOAD_ROOT", str(root))
    return api, TestClient


def test_an_owners_files_are_reindexed_at_startup(monkeypatch, tmp_path):
    """The whole point: after a restart the documents are usable again."""
    added = []

    import modules.rag as _rag

    class SpyRAG(_rag.SimpleRAG):
        def add_document(self, path):
            added.append(os.path.basename(path))
            return super().add_document(path)

        def has_document(self, name):
            return False

    # `lifespan` does `from modules.rag import SimpleRAG` INSIDE the function,
    # so patching `api.SimpleRAG` would silently do nothing - the name is
    # resolved from modules.rag at call time. Patch the source.
    import modules.rag as _rag_mod
    monkeypatch.setattr(_rag_mod, "SimpleRAG", SpyRAG, raising=False)
    api, TestClient = _boot(monkeypatch, tmp_path,
                            {"owner-1/alpha.txt": "alpha content",
                             "owner-1/beta.txt": "beta content"})

    with TestClient(api.app):
        pass
    assert sorted(added) == ["alpha.txt", "beta.txt"], added


def test_a_file_already_indexed_is_skipped(monkeypatch, tmp_path):
    """Restarts must not re-embed everything — that costs money and time."""
    added = []

    import modules.rag as _rag

    class SpyRAG(_rag.SimpleRAG):
        def add_document(self, path):
            added.append(os.path.basename(path))
            return "added"

        def has_document(self, name):
            return name == "already.txt"

    # `lifespan` does `from modules.rag import SimpleRAG` INSIDE the function,
    # so patching `api.SimpleRAG` would silently do nothing - the name is
    # resolved from modules.rag at call time. Patch the source.
    import modules.rag as _rag_mod
    monkeypatch.setattr(_rag_mod, "SimpleRAG", SpyRAG, raising=False)
    api, TestClient = _boot(monkeypatch, tmp_path,
                            {"owner-1/already.txt": "x",
                             "owner-1/new.txt": "y"})

    with TestClient(api.app):
        pass
    assert added == ["new.txt"], (
        f"an already-indexed document was re-embedded on boot: {added}"
    )


def test_an_unowned_legacy_file_is_reported_and_never_indexed(monkeypatch, tmp_path, capsys):
    """A file at the top level has no owner. Indexing it would put it in a
    namespace nobody owns; the correct behaviour is to warn and skip."""
    added = []

    import modules.rag as _rag

    class SpyRAG(_rag.SimpleRAG):
        def add_document(self, path):
            added.append(os.path.basename(path))
            return "added"

        def has_document(self, name):
            return False

    # `lifespan` does `from modules.rag import SimpleRAG` INSIDE the function,
    # so patching `api.SimpleRAG` would silently do nothing - the name is
    # resolved from modules.rag at call time. Patch the source.
    import modules.rag as _rag_mod
    monkeypatch.setattr(_rag_mod, "SimpleRAG", SpyRAG, raising=False)
    api, TestClient = _boot(monkeypatch, tmp_path,
                            {"legacy-orphan.txt": "nobody owns this",
                             "owner-1/owned.txt": "fine"})

    with TestClient(api.app):
        pass
    out = capsys.readouterr().out
    assert "legacy upload" in out.lower(), f"the orphan was not reported: {out!r}"
    assert "legacy-orphan.txt" not in added, (
        "an unowned file was indexed into someone's namespace"
    )
    assert "owned.txt" in added


def test_one_unreadable_file_does_not_stop_the_others(monkeypatch, tmp_path, capsys):
    """A corrupt document must not abort the whole re-index.

    This is the difference between losing one file and losing everyone's library
    on every restart.
    """
    added = []

    import modules.rag as _rag

    class SpyRAG(_rag.SimpleRAG):
        def add_document(self, path):
            name = os.path.basename(path)
            if name == "broken.txt":
                raise ValueError("cannot parse")
            added.append(name)
            return "added"

        def has_document(self, name):
            return False

    # `lifespan` does `from modules.rag import SimpleRAG` INSIDE the function,
    # so patching `api.SimpleRAG` would silently do nothing - the name is
    # resolved from modules.rag at call time. Patch the source.
    import modules.rag as _rag_mod
    monkeypatch.setattr(_rag_mod, "SimpleRAG", SpyRAG, raising=False)
    api, TestClient = _boot(monkeypatch, tmp_path,
                            {"owner-1/broken.txt": "x",
                             "owner-1/good.txt": "y"})

    with TestClient(api.app):
        pass
    out = capsys.readouterr().out
    assert "Failed to re-index" in out, "the failure was not reported"
    assert "good.txt" in added, "one broken file stopped the rest of the walk"


def test_a_missing_uploads_directory_is_not_an_error(monkeypatch, tmp_path):
    """A fresh clone has no uploads/ yet; startup must not care."""
    api, TestClient = _boot(monkeypatch, tmp_path, {})
    # Point at a path that does not exist at all.
    monkeypatch.setattr(api, "UPLOAD_ROOT", str(tmp_path / "does-not-exist"))
    with TestClient(api.app) as client:
        assert client.get("/health").status_code in (200, 503)


def test_a_subdirectory_is_not_mistaken_for_a_file(monkeypatch, tmp_path):
    """`uploads/<owner>/<nested>/` must not be handed to add_document()."""
    added = []

    import modules.rag as _rag

    class SpyRAG(_rag.SimpleRAG):
        def add_document(self, path):
            added.append(os.path.basename(path))
            return "added"

        def has_document(self, name):
            return False

    # `lifespan` does `from modules.rag import SimpleRAG` INSIDE the function,
    # so patching `api.SimpleRAG` would silently do nothing - the name is
    # resolved from modules.rag at call time. Patch the source.
    import modules.rag as _rag_mod
    monkeypatch.setattr(_rag_mod, "SimpleRAG", SpyRAG, raising=False)
    api, TestClient = _boot(monkeypatch, tmp_path,
                            {"owner-1/file.txt": "a",
                             "owner-1/nested/inner.txt": "b"})

    with TestClient(api.app):
        pass
    assert "nested" not in added, f"a directory was passed to add_document: {added}"
