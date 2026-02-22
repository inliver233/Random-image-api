from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all model modules so Base.metadata is fully populated for create_all().
# This keeps tests and one-off scripts consistent, regardless of import order.
from app.db.models import admin_audit as _admin_audit  # noqa: F401,E402
from app.db.models import api_keys as _api_keys  # noqa: F401,E402
from app.db.models import hydration_runs as _hydration_runs  # noqa: F401,E402
from app.db.models import image_tags as _image_tags  # noqa: F401,E402
from app.db.models import images as _images  # noqa: F401,E402
from app.db.models import imports as _imports  # noqa: F401,E402
from app.db.models import jobs as _jobs  # noqa: F401,E402
from app.db.models import pixiv_tokens as _pixiv_tokens  # noqa: F401,E402
from app.db.models import proxy_endpoints as _proxy_endpoints  # noqa: F401,E402
from app.db.models import proxy_pool_endpoints as _proxy_pool_endpoints  # noqa: F401,E402
from app.db.models import proxy_pools as _proxy_pools  # noqa: F401,E402
from app.db.models import request_logs as _request_logs  # noqa: F401,E402
from app.db.models import runtime_settings as _runtime_settings  # noqa: F401,E402
from app.db.models import tags as _tags  # noqa: F401,E402
from app.db.models import token_proxy_bindings as _token_proxy_bindings  # noqa: F401,E402
