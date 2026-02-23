from __future__ import annotations

from app.core.pximg_reverse_proxy import pick_pximg_mirror_host_for_request


def test_pick_pximg_mirror_host_for_request_prefers_re_for_cn() -> None:
    host = pick_pximg_mirror_host_for_request(headers={"CF-IPCountry": "CN"}, fallback_host="i.pixiv.cat")
    assert host == "i.pixiv.re"


def test_pick_pximg_mirror_host_for_request_uses_fallback_for_non_cn() -> None:
    host = pick_pximg_mirror_host_for_request(headers={"CF-IPCountry": "US"}, fallback_host="i.pixiv.nl")
    assert host == "i.pixiv.nl"


def test_pick_pximg_mirror_host_for_request_falls_back_to_default_on_invalid_fallback() -> None:
    host = pick_pximg_mirror_host_for_request(headers=None, fallback_host="not-a-host")
    assert host == "i.pixiv.cat"

