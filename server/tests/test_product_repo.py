from app.db.product_repo import _image_url, _normalize_static_base_url


def test_normalize_static_base_url_falls_back_when_placeholder():
    assert _normalize_static_base_url("your-ip-address", port=8000) == "http://127.0.0.1:8000"


def test_normalize_static_base_url_adds_http_scheme_for_host_only_value():
    assert _normalize_static_base_url("121.196.247.225", port=8000) == "http://121.196.247.225"


def test_image_url_uses_normalized_static_base_url():
    base = _normalize_static_base_url("http://127.0.0.1:8000/", port=8000)
    assert _image_url("1_美妆护肤/images/p_beauty_001_live.jpg", base) == (
        "http://127.0.0.1:8000/static/1_美妆护肤/images/p_beauty_001_live.jpg"
    )
