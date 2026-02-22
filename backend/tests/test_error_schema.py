from app.core.errors import ErrorCode, UNKNOWN_REQUEST_ID, error_body


def test_error_body_shape() -> None:
    body = error_body(code=ErrorCode.BAD_REQUEST, message="nope", request_id="req_123", details={"a": 1})
    assert body["ok"] is False
    assert body["code"] == "BAD_REQUEST"
    assert body["message"] == "请求参数错误"
    assert body["request_id"] == "req_123"
    assert body["details"] == {"a": 1}


def test_error_body_request_id_default() -> None:
    body = error_body(code=ErrorCode.INTERNAL_ERROR, message="boom", request_id=None, details=None)
    assert body["request_id"] == UNKNOWN_REQUEST_ID
