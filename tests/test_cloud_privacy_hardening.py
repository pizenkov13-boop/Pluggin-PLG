from cloud.app.beat_data import upsert_beat
from cloud.app.privacy import redact_sensitive_text


def test_redaction_removes_common_personal_and_secret_values():
    value = "mail me at user@example.com password=supersecret C:\\Users\\Dima\\file.log"
    safe = redact_sensitive_text(value)
    assert "user@example.com" not in safe
    assert "supersecret" not in safe
    assert "Dima" not in safe


def test_beat_sync_never_persists_prompt_preview():
    class Response:
        data = None

    class Table:
        def __init__(self, name):
            self.name = name
            self.inserted = None

        def select(self, *_args): return self
        def eq(self, *_args): return self
        def maybe_single(self): return self
        def upsert(self, row, **_kwargs):
            self.inserted = row
            return self
        def execute(self): return Response()
        def insert(self, *_args, **_kwargs): return self

    tables = {}
    class Client:
        def table(self, name):
            tables.setdefault(name, Table(name))
            return tables[name]

    upsert_beat(Client(), "user", {"beat_id": "b1", "prompt_preview": "private text"})
    assert tables["beats"].inserted["prompt_preview"] is None
