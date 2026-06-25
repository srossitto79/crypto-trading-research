from axiom.db import kv_get


def test_kv_get_returns_default_before_schema_bootstrap():
    default = {"ready": False}

    assert kv_get("axiom:notification_preferences", default) is default
