from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import ApiError
from app.core.proxy_mirror import resolve_proxy_mirror


def _runtime(**kwargs):
    base = {
        "image_proxy_use_pixiv_cat": False,
        "image_proxy_pximg_mirror_host": "i.pixiv.cat",
        "image_proxy_extra_pximg_mirror_hosts": [],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_resolve_proxy_mirror_defaults_to_runtime_host() -> None:
    r = resolve_proxy_mirror(runtime=_runtime(), pixiv_cat=0)
    assert r.proxy_override is None
    assert r.pximg_mirror_host_override is None
    assert r.use_pixiv_cat is False
    assert r.mirror_host == "i.pixiv.cat"


def test_resolve_proxy_mirror_pixiv_cat_enables_mirror() -> None:
    r = resolve_proxy_mirror(runtime=_runtime(), pixiv_cat=1)
    assert r.use_pixiv_cat is True
    assert r.mirror_host == "i.pixiv.cat"


def test_resolve_proxy_mirror_proxy_override() -> None:
    r = resolve_proxy_mirror(runtime=_runtime(), proxy="i.pixiv.re")
    assert r.proxy_override == "i.pixiv.re"
    assert r.use_pixiv_cat is True
    assert r.mirror_host == "i.pixiv.re"


def test_resolve_proxy_mirror_invalid_proxy_raises() -> None:
    with pytest.raises(ApiError) as ei:
        resolve_proxy_mirror(runtime=_runtime(), proxy="evil.example.com")
    assert ei.value.status_code == 400


def test_resolve_proxy_mirror_invalid_proxy_soft() -> None:
    r = resolve_proxy_mirror(runtime=_runtime(), proxy="evil.example.com", raise_on_invalid=False)
    assert r.proxy_override is None
    assert r.use_pixiv_cat is False


def test_resolve_proxy_mirror_runtime_flag() -> None:
    r = resolve_proxy_mirror(runtime=_runtime(image_proxy_use_pixiv_cat=True), pixiv_cat=0)
    assert r.use_pixiv_cat is True
