"""Model acceptance must bind its isolated server before any write or key use."""

import importlib.util
import concurrent.futures
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.models import DataTableRecord


ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location("isolation_" + name.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fixture(workspace):
    data = workspace / ".local/run/data"
    data.mkdir(parents=True)
    engine = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            for name in ("ask-synthetic", "scale-1000", "scale-10000"):
                session.add(DataTableRecord(id=name, name=name, template_key=f"manual:{name}",
                                           template_version="1", document_kind="custom", columns_json="[]"))
            session.commit()
        with Session(engine) as session:
            original_ids = set(session.query(DataTableRecord.id).all())
        return data, original_ids
    finally:
        engine.dispose()


def test_fixture_disposes_engine_after_setup_failure(tmp_path, monkeypatch):
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setitem(globals(), "build_engine", lambda *_args: engine)
    def fail_setup(_engine):
        raise RuntimeError("synthetic schema setup failure")
    monkeypatch.setattr(Base.metadata, "create_all", fail_setup)
    with pytest.raises(RuntimeError, match="synthetic schema setup failure"):
        create_fixture(tmp_path / "workspace")
    assert disposed == [True]


@pytest.mark.parametrize("case", ["wrong_server", "linked_sidecar"])
def test_assertion_read_disposes_reopened_engine_after_failure(tmp_path, monkeypatch, case):
    engines = []
    real_build, real_session = build_engine, Session
    def tracked_build(*args, **kwargs):
        engine = real_build(*args, **kwargs)
        record = {"disposed": False}
        engines.append(record)
        dispose = engine.dispose
        def tracked_dispose(*args, **kwargs):
            record["disposed"] = True
            return dispose(*args, **kwargs)
        monkeypatch.setattr(engine, "dispose", tracked_dispose)
        return engine
    sessions = 0
    def fail_read(engine):
        nonlocal sessions
        sessions += 1
        if sessions == 3:
            raise RuntimeError("synthetic assertion read failure")
        return real_session(engine)
    monkeypatch.setitem(globals(), "build_engine", tracked_build)
    monkeypatch.setitem(globals(), "Session", fail_read)
    with pytest.raises(RuntimeError, match="synthetic assertion read failure"):
        if case == "wrong_server":
            test_wrong_local_server_does_not_receive_key_or_any_write(tmp_path, monkeypatch, "verify-v050-model.py")
        else:
            test_linked_database_sidecar_is_rejected_before_client_or_key(
                tmp_path, monkeypatch, "verify-v050-model.py", "-wal", "is_symlink")
    assert len(engines) == 2
    assert all(record["disposed"] for record in engines)


def arguments(name, data, output, url, key):
    if name == "verify-v050-model.py":
        return [name, "--data-dir", str(data), "--url", url, "--model", "synthetic-model",
                "--key-file", str(key), "--output", str(output)]
    return [name, "--data-dir", str(data), "--url", url, "--model", "synthetic-model",
            "--key-file", str(key), "--pid", "123", "--output", str(output)]


@pytest.mark.parametrize("name", ["verify-v050-model.py", "verify-assistant-scale.py"])
def test_wrong_local_server_does_not_receive_key_or_any_write(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    workspace = tmp_path / "workspace"
    data, original_ids = create_fixture(workspace)
    output = workspace / ".local/run/result.json"
    key = workspace / ".local/run/synthetic-key.txt"
    key.write_text("test-only-placeholder", encoding="utf-8")
    module = load_script(name)
    monkeypatch.setattr(module, "__file__", str(workspace / "scripts" / name))
    monkeypatch.setattr(sys, "argv", arguments(name, data, output, "http://127.0.0.1:18814", key))
    key_reads, writes = [], []
    read_text = Path.read_text
    def audited_read(path, *args, **kwargs):
        if path == key:
            key_reads.append(True)
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", audited_read)
    if hasattr(module, "OpenAICompatibleProvider"):
        monkeypatch.setattr(module, "OpenAICompatibleProvider", lambda *_args, **_kwargs: object())
    class Response:
        status_code = 404
        def raise_for_status(self): raise RuntimeError("synthetic wrong server")
        def json(self): return {}
    class Client:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, *_args, **_kwargs): return Response()
        def post(self, *args, **_kwargs):
            writes.append(args)
            raise RuntimeError("synthetic server must not be written")
    monkeypatch.setattr(module.httpx, "Client", Client)
    with pytest.raises((RuntimeError, ValueError, SystemExit)):
        module.main()
    assert not writes
    assert not key_reads
    engine = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    try:
        with Session(engine) as session:
            assert set(session.query(DataTableRecord.id).all()) == original_ids
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", ["verify-v050-model.py", "verify-assistant-scale.py"])
def test_non_loopback_url_is_rejected_without_opening_a_client(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    workspace = tmp_path / "workspace"
    data, _ = create_fixture(workspace)
    output = workspace / ".local/run/result.json"
    key = workspace / ".local/run/synthetic-key.txt"
    key.write_text("test-only-placeholder", encoding="utf-8")
    module = load_script(name)
    monkeypatch.setattr(module, "__file__", str(workspace / "scripts" / name))
    monkeypatch.setattr(sys, "argv", arguments(name, data, output, "https://outside.invalid", key))
    if hasattr(module, "OpenAICompatibleProvider"):
        monkeypatch.setattr(module, "OpenAICompatibleProvider", lambda *_args, **_kwargs: object())
    opened = []
    def client(*_args, **_kwargs):
        opened.append(True)
        raise RuntimeError("synthetic client refused")
    monkeypatch.setattr(module.httpx, "Client", client)
    with pytest.raises((RuntimeError, ValueError, SystemExit)):
        module.main()
    assert not opened


def test_scale_existing_report_is_preserved_before_opening_client(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    workspace = tmp_path / "workspace"
    data, _ = create_fixture(workspace)
    output = workspace / ".local/run/result.json"
    output.write_bytes(b"existing synthetic report")
    key = workspace / ".local/run/synthetic-key.txt"
    module = load_script("verify-assistant-scale.py")
    monkeypatch.setattr(module, "__file__", str(workspace / "scripts/verify-assistant-scale.py"))
    monkeypatch.setattr(sys, "argv", arguments("verify-assistant-scale.py", data, output,
                                                "http://127.0.0.1:18814", key))
    opened = []
    def client(*_args, **_kwargs):
        opened.append(True)
        raise RuntimeError("synthetic client refused")
    monkeypatch.setattr(module.httpx, "Client", client)
    with pytest.raises((FileExistsError, RuntimeError, ValueError, SystemExit)):
        module.main()
    assert output.read_bytes() == b"existing synthetic report"
    assert not opened


@pytest.mark.parametrize("name", ["verify-v050-model.py", "verify-assistant-scale.py"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("predicate", ["is_symlink", "is_junction"])
def test_linked_database_sidecar_is_rejected_before_client_or_key(tmp_path, monkeypatch, name, suffix, predicate):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    workspace = tmp_path / "workspace"
    data, original_ids = create_fixture(workspace)
    output = workspace / ".local/run/result.json"
    key = workspace / ".local/run/synthetic-key.txt"
    module = load_script(name)
    monkeypatch.setattr(module, "__file__", str(workspace / "scripts" / name))
    monkeypatch.setattr(sys, "argv", arguments(name, data, output, "http://127.0.0.1:18814", key))
    target = data / ("document-pipeline.db" + suffix)
    original = getattr(Path, predicate)
    monkeypatch.setattr(Path, predicate, lambda path: path == target or original(path))
    opened, key_reads = [], []
    monkeypatch.setattr(module.httpx, "Client", lambda *_args, **_kwargs: opened.append(True))
    read_text = Path.read_text
    def audited_read(path, *args, **kwargs):
        if path == key:
            key_reads.append(True)
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", audited_read)
    with pytest.raises(ValueError, match="sidecars"):
        module.main()
    assert not opened and not key_reads and not output.exists()
    engine = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    try:
        with Session(engine) as session:
            assert set(session.query(DataTableRecord.id).all()) == original_ids
    finally:
        engine.dispose()


def test_regular_database_sidecars_are_not_changed_by_path_validation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_assistant_isolation import validate_paths
    workspace = tmp_path / "workspace"
    data, _ = create_fixture(workspace)
    sidecars = [data / ("document-pipeline.db" + suffix) for suffix in ("-wal", "-shm", "-journal")]
    for index, path in enumerate(sidecars):
        path.write_bytes(f"synthetic-sidecar-{index}".encode())
    before = [path.read_bytes() for path in sidecars]
    validate_paths(workspace, data, workspace / ".local/run/result.json")
    assert [path.read_bytes() for path in sidecars] == before


@pytest.mark.parametrize("name", ["verify-v050-model.py", "verify-assistant-scale.py"])
def test_report_outside_workspace_is_rejected_before_any_client(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    workspace = tmp_path / "workspace"
    data, _ = create_fixture(workspace)
    output = tmp_path / "outside-result.json"
    module = load_script(name)
    monkeypatch.setattr(module, "__file__", str(workspace / "scripts" / name))
    monkeypatch.setattr(sys, "argv", arguments(name, data, output, "http://127.0.0.1:18814", tmp_path / "key"))
    opened = []
    monkeypatch.setattr(module.httpx, "Client", lambda *_args, **_kwargs: opened.append(True))
    with pytest.raises((RuntimeError, ValueError, SystemExit)):
        module.main()
    assert not opened and not output.exists()


@pytest.mark.parametrize("url", ["https://127.0.0.1:1234", "http://user@127.0.0.1:1234",
                                "http://127.0.0.1:1234/path", "http://127.0.0.1:1234/?x=1",
                                "http://127.0.0.1", "http://127.0.0.1:0", "http://example.invalid:1234"])
def test_url_parser_rejects_nonlocal_or_ambiguous_urls(monkeypatch, url):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_assistant_isolation import validate_loopback_url
    with pytest.raises(ValueError):
        validate_loopback_url(url)


def test_two_concurrent_server_challenges_are_unique_and_leave_existing_tables(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_assistant_isolation import require_bound_server
    from document_pipeline_api.services.data_tables import get_data_table
    data, original_ids = create_fixture(tmp_path / "workspace")
    engine = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    barrier = threading.Barrier(2)
    seen = []
    class Client:
        def get(self, path, **kwargs):
            assert kwargs["follow_redirects"] is False
            identifier = path.rsplit("/", 1)[-1]
            with Session(engine) as session:
                value = get_data_table(session, identifier).model_dump(mode="json")
            seen.append((value["id"], value["name"]))
            barrier.wait(timeout=10)
            return SimpleNamespace(status_code=200, history=[], json=lambda: value)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(require_bound_server, engine, Client()) for _ in range(2)]
            for future in futures:
                future.result(timeout=15)
        assert len(seen) == 2 and len({value[0] for value in seen}) == 2 and len({value[1] for value in seen}) == 2
        with Session(engine) as session:
            assert set(session.query(DataTableRecord.id).all()) == original_ids
    finally:
        engine.dispose()


@pytest.mark.parametrize("same_database", [True, False])
def test_mock_http_challenge_binds_selected_database_without_posts(tmp_path, monkeypatch, same_database):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_assistant_isolation import require_bound_server
    from document_pipeline_api.services.data_tables import get_data_table
    data, original_ids = create_fixture(tmp_path / "selected")
    other_data, other_ids = create_fixture(tmp_path / "other")
    selected = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    other = build_engine(f"sqlite:///{(other_data / 'document-pipeline.db').as_posix()}")
    server = selected if same_database else other
    requests = []
    def handle(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET" and request.url.path.startswith("/api/v1/tables/")
        identifier = request.url.path.rsplit("/", 1)[-1]
        with Session(server) as session:
            if session.get(DataTableRecord, identifier) is None:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=get_data_table(session, identifier).model_dump(mode="json"))
    try:
        with httpx.Client(base_url="http://127.0.0.1:18814/api/v1", transport=httpx.MockTransport(handle),
                          follow_redirects=False) as client:
            if same_database:
                require_bound_server(selected, client)
            else:
                with pytest.raises(ValueError):
                    require_bound_server(selected, client)
        assert len(requests) == 1 and requests[0][0] == "GET"
        with Session(selected) as session:
            assert set(session.query(DataTableRecord.id).all()) == original_ids
        with Session(other) as session:
            assert set(session.query(DataTableRecord.id).all()) == other_ids
    finally:
        selected.dispose()
        other.dispose()


@pytest.mark.parametrize("name", ["verify-v050-model.py", "verify-assistant-scale.py"])
def test_matching_server_allows_key_use_only_after_identity_check(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from document_pipeline_api.services.data_tables import get_data_table
    workspace = tmp_path / "workspace"
    data, original_ids = create_fixture(workspace)
    output = workspace / ".local/run/result.json"
    key = workspace / ".local/run/synthetic-key.txt"
    key.write_text("test-only-placeholder", encoding="utf-8")
    module = load_script(name)
    monkeypatch.setattr(module, "__file__", str(workspace / "scripts" / name))
    monkeypatch.setattr(sys, "argv", arguments(name, data, output, "http://127.0.0.1:18814", key))
    engine = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    operations = []
    read_text = Path.read_text
    def audited_read(path, *args, **kwargs):
        if path == key:
            operations.append("key")
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", audited_read)
    class StopAfterIdentity(Exception):
        pass
    def provider(*_args, **_kwargs):
        operations.append("provider")
        raise StopAfterIdentity()
    if hasattr(module, "OpenAICompatibleProvider"):
        monkeypatch.setattr(module, "OpenAICompatibleProvider", provider)
    class Client:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, path, **_kwargs):
            operations.append("identity")
            with Session(engine) as session:
                value = get_data_table(session, path.rsplit("/", 1)[-1]).model_dump(mode="json")
            return SimpleNamespace(status_code=200, history=[], json=lambda: value)
        def post(self, *_args, **_kwargs):
            operations.append("post")
            raise StopAfterIdentity()
    monkeypatch.setattr(module.httpx, "Client", Client)
    try:
        with pytest.raises(StopAfterIdentity):
            module.main()
        assert operations[:2] == ["identity", "key"]
        assert operations[-1] == ("provider" if name == "verify-v050-model.py" else "post")
        assert output.is_file()
        with Session(engine) as session:
            assert set(session.query(DataTableRecord.id).all()) == original_ids
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["redirect", "cached"])
def test_redirect_or_cached_response_never_passes_challenge(tmp_path, monkeypatch, mode):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_assistant_isolation import require_bound_server
    data, original_ids = create_fixture(tmp_path / "workspace")
    engine = build_engine(f"sqlite:///{(data / 'document-pipeline.db').as_posix()}")
    class Client:
        def get(self, path, **kwargs):
            assert kwargs["follow_redirects"] is False
            if mode == "redirect":
                return SimpleNamespace(status_code=302, history=[], json=lambda: {})
            return SimpleNamespace(status_code=200, history=[],
                                   json=lambda: {"id": path.rsplit("/", 1)[-1], "name": "old cached value"})
    try:
        with pytest.raises(ValueError):
            require_bound_server(engine, Client())
        with Session(engine) as session:
            assert set(session.query(DataTableRecord.id).all()) == original_ids
    finally:
        engine.dispose()
