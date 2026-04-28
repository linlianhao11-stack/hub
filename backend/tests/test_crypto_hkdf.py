def test_hkdf_derive_distinct_keys_per_purpose():
    """同 master key + 不同 purpose → 不同子密钥。"""
    from hub.crypto.hkdf import derive_key
    master = b"\x01" * 32
    k1 = derive_key(master, purpose="config_secrets")
    k2 = derive_key(master, purpose="task_payload")
    assert k1 != k2
    assert len(k1) == 32
    assert len(k2) == 32


def test_hkdf_deterministic():
    """同输入产出同输出（无随机性）。"""
    from hub.crypto.hkdf import derive_key
    master = b"\xab" * 32
    k1 = derive_key(master, purpose="x")
    k2 = derive_key(master, purpose="x")
    assert k1 == k2


def test_hkdf_different_master_yields_different():
    from hub.crypto.hkdf import derive_key
    k1 = derive_key(b"\x01" * 32, purpose="x")
    k2 = derive_key(b"\x02" * 32, purpose="x")
    assert k1 != k2
