import json
import logging

from app.observability.logging import JsonFormatter, request_id_var


def test_json_formatter_includes_extra_and_request_id():
    token = request_id_var.set("rid-1")
    try:
        rec = logging.makeLogRecord(
            {"msg": "hello %s", "args": ("x",), "levelname": "INFO", "name": "t", "source": "dtp"}
        )
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "hello x"
    assert out["request_id"] == "rid-1"
    assert out["source"] == "dtp"
