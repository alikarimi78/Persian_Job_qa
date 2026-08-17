"""Narrowed models, generated into `prisma.partials` by `prisma generate`.

This file exists for one column. `organizations.logo` holds the image itself, and
Prisma's Python client has no per-query `select`: `Organization.prisma().find_many()`
asks for every scalar field the model declares, so a bare read of the organization list
would pull one image per row out of Postgres for a response that does not carry them.
SQLAlchemy solved that with `deferred=True` on the column; here the *model* is what
narrows the query, and `OrganizationSummary` is the one used everywhere except
`GET /orgs/{id}/logo`.

`has_logo` in `OrganizationOut` is read off `logo_mime`, which is kept here precisely so
the cheap column can answer for the expensive one. The two are only ever written together
(`src/routers/orgs.py:apply_logo`), which is what makes that answer truthful.
"""

from prisma.models import Organization

Organization.create_partial("OrganizationSummary", exclude={"logo"})
