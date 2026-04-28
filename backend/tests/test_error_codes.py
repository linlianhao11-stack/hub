def test_known_codes_have_zh_messages():
    from hub.error_codes import ERROR_MESSAGES, BizErrorCode
    must_have = [
        BizErrorCode.BIND_USER_NOT_FOUND, BizErrorCode.BIND_CODE_INVALID,
        BizErrorCode.USER_NOT_BOUND, BizErrorCode.USER_ERP_DISABLED,
        BizErrorCode.PERM_NO_PRODUCT_QUERY, BizErrorCode.PERM_DOWNSTREAM_DENIED,
        BizErrorCode.MATCH_NOT_FOUND, BizErrorCode.MATCH_AMBIGUOUS,
        BizErrorCode.INTENT_LOW_CONFIDENCE, BizErrorCode.ERP_TIMEOUT,
        BizErrorCode.ERP_CIRCUIT_OPEN, BizErrorCode.INTERNAL_ERROR,
    ]
    for code in must_have:
        assert code in ERROR_MESSAGES
        assert ERROR_MESSAGES[code]
        assert code.value not in ERROR_MESSAGES[code]


def test_user_friendly_message_supports_template():
    from hub.error_codes import BizErrorCode, build_user_message
    msg = build_user_message(
        BizErrorCode.MATCH_NOT_FOUND, keyword="阿里", resource="客户",
    )
    assert "阿里" in msg
    assert "客户" in msg
    assert "MATCH_NOT_FOUND" not in msg


def test_unknown_code_falls_back_internal_error():
    from hub.error_codes import build_user_message
    msg = build_user_message("BOGUS_CODE_NOT_DEFINED")
    assert "出错" in msg or "异常" in msg
