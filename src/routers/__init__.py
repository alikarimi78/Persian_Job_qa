# One package per API surface. Each holds `router.py` (the endpoints), `schemas.py`
# (the request/response models that surface uses) and, where there is logic worth
# keeping out of the handlers, `service.py`. The package re-exports `router`, so
# `from src.routers import admin; admin.router` still names the APIRouter.
