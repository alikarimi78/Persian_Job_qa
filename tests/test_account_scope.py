"""`src/accounts.py`: the scope rules, tested straight rather than through HTTP.

Every one of these is "may this caller act on that record", and the answer is always the
provisioning chain read downwards — you may act on the accounts you could have created.
The API tests in `test_accounts_api.py` check that the endpoints ask these questions;
this file checks the answers.
"""

import pytest
from fastapi import HTTPException
from prisma.errors import PrismaError

from src.accounts import (assert_can_manage_account, assert_manages_organization,
                          assert_manages_unit, create_account, delete_account,
                          move_to_unit, visible_users)
from src.models import JobStatus, Role


def refuses(fn, *args, status=403):
    with pytest.raises(HTTPException) as raised:
        fn(*args)
    assert raised.value.status_code == status
    return raised.value


# ---------- assert_manages_organization ----------

def test_a_super_admin_manages_every_organization(world):
    assert_manages_organization(world.root, world.org_a.id)
    assert_manages_organization(world.root, world.org_b.id)


def test_an_org_admin_manages_only_its_own(world):
    assert_manages_organization(world.admin_a, world.org_a.id)
    refuses(assert_manages_organization, world.admin_a, world.org_b.id)


@pytest.mark.parametrize("account", ["admin_a1", "user_a1"])
def test_below_an_org_admin_nobody_manages_an_organization(world, account):
    refuses(assert_manages_organization, getattr(world, account), world.org_a.id)


# ---------- assert_manages_unit ----------

def test_a_super_admin_manages_every_unit(world):
    assert_manages_unit(world.root, world.unit_a1)
    assert_manages_unit(world.root, world.unit_b1)


def test_an_org_admin_manages_the_units_of_its_organization(world):
    assert_manages_unit(world.admin_a, world.unit_a1)
    assert_manages_unit(world.admin_a, world.unit_a2)
    refuses(assert_manages_unit, world.admin_a, world.unit_b1)


def test_a_unit_admin_manages_one_unit(world):
    assert_manages_unit(world.admin_a1, world.unit_a1)
    # a sibling unit of the same organization is still not theirs
    refuses(assert_manages_unit, world.admin_a1, world.unit_a2)


def test_an_ordinary_user_manages_no_unit(world):
    refuses(assert_manages_unit, world.user_a1, world.unit_a1)


# ---------- assert_can_manage_account ----------

@pytest.mark.parametrize("account", ["root", "admin_a", "admin_a1", "user_a1"])
def test_nobody_may_act_on_their_own_account(world, account):
    """At any level, deliberately: an admin who blocked themselves would need someone
    above them to undo it, and a unit_admin has nobody above them inside their unit."""
    actor = getattr(world, account)
    refuses(assert_can_manage_account, actor, actor)


@pytest.mark.parametrize("target", ["admin_a", "admin_a1", "user_a1", "user_b1"])
def test_a_super_admin_may_act_on_anyone_else(world, target):
    assert_can_manage_account(world.root, getattr(world, target))


def test_an_org_admin_reaches_everyone_inside_its_organization(world):
    assert_can_manage_account(world.admin_a, world.admin_a1)
    assert_can_manage_account(world.admin_a, world.user_a1)
    assert_can_manage_account(world.admin_a, world.user_a2)


def test_an_org_admin_reaches_nothing_outside_it(world):
    refuses(assert_can_manage_account, world.admin_a, world.admin_b1)
    refuses(assert_can_manage_account, world.admin_a, world.user_b1)


def test_an_org_admin_may_not_act_on_a_peer_or_on_a_super_admin(world):
    """Both live outside any unit, which is exactly the test the rule makes: an
    org_admin reaches accounts *through* the units of its organization."""
    refuses(assert_can_manage_account, world.admin_a, world.admin_b)
    refuses(assert_can_manage_account, world.admin_a, world.root)


def test_a_unit_admin_reaches_the_ordinary_users_of_its_unit(world):
    assert_can_manage_account(world.admin_a1, world.user_a1)
    assert_can_manage_account(world.admin_a1, world.user_a1b)


def test_a_unit_admin_reaches_neither_another_unit_nor_another_admin(world):
    refuses(assert_can_manage_account, world.admin_a1, world.user_a2)
    refuses(assert_can_manage_account, world.admin_a1, world.admin_a2)
    # not even a unit_admin sitting in their own unit, were there one
    refuses(assert_can_manage_account, world.admin_a1, world.admin_a)


def test_an_ordinary_user_may_act_on_nobody(world):
    refuses(assert_can_manage_account, world.user_a1, world.user_a1b)


# ---------- visible_users ----------

def test_a_super_admin_sees_every_account(world, db):
    assert len(visible_users(db, world.root)) == db.user.count()


def test_an_org_admin_sees_the_accounts_of_its_units(world, db):
    seen = {u.username for u in visible_users(db, world.admin_a)}
    assert seen == {"admin-a1", "admin-a2", "user-a1", "user-a1b", "user-a2"}
    # itself included: an org_admin has no unit_id, and the listing is by unit
    assert "admin-a" not in seen


def test_a_unit_admin_sees_its_own_roster(world, db):
    seen = {u.username for u in visible_users(db, world.admin_a1)}
    assert seen == {"admin-a1", "user-a1", "user-a1b"}


# ---------- create_account ----------

def test_usernames_are_global_not_per_tenant(world, db):
    """Login is by username alone, so it has to identify exactly one row anywhere."""
    refuses(lambda: create_account(db, username="user-a1", password="password12",
                                   role=Role.user, unit_id=world.unit_b1.id), status=409)


def test_an_organization_holds_one_admin(world, db):
    error = refuses(lambda: create_account(db, username="second-a", password="password12",
                                           role=Role.org_admin,
                                           organization_id=world.org_a.id), status=409)
    # the 409 names the sitting admin rather than leaving the caller to guess
    assert "admin-a" in error.detail


def test_a_unit_holds_one_admin(world, db):
    error = refuses(lambda: create_account(db, username="second-a1", password="password12",
                                           role=Role.unit_admin,
                                           unit_id=world.unit_a1.id), status=409)
    assert "admin-a1" in error.detail


def test_a_unit_holds_as_many_ordinary_users_as_it_likes(world, db):
    """The uniqueness above is a *partial* index; without its WHERE clause this is the
    case that would fail."""
    made = create_account(db, username="user-a1c", password="password12", role=Role.user,
                          unit_id=world.unit_a1.id)
    assert made.unit_id == world.unit_a1.id
    assert made.organization_id is None


def test_a_created_account_carries_exactly_its_own_scope_column(world, db):
    """`ck_users_scope`: a unit_admin's organization is reached through its unit and is
    never stored twice, so the two cannot drift."""
    fresh = db.unit.create(data={"name": "unit-a3", "organization_id": world.org_a.id})

    admin = create_account(db, username="admin-a3", password="password12",
                           role=Role.unit_admin, unit_id=fresh.id)
    assert admin.unit_id == fresh.id
    assert admin.organization_id is None

    org_c = db.organization.create(data={"name": "org-c"})

    org_admin = create_account(db, username="admin-c", password="password12",
                               role=Role.org_admin, organization_id=org_c.id)
    assert org_admin.organization_id == org_c.id
    assert org_admin.unit_id is None


# ---------- move_to_unit ----------

def test_a_user_moves_and_keeps_its_role(world, db):
    moved = move_to_unit(db, world.user_a1, world.unit_a2)
    assert moved.unit_id == world.unit_a2.id
    assert moved.role == Role.user


def test_an_account_that_lives_in_no_unit_cannot_move(world, db):
    """An org_admin belongs to an organization and a super_admin to nothing, so neither
    has anywhere to go."""
    refuses(lambda: move_to_unit(db, world.admin_a, world.unit_a1), status=409)
    refuses(lambda: move_to_unit(db, world.root, world.unit_a1), status=409)


def test_a_unit_admin_may_not_land_on_a_sitting_admin(world, db):
    error = refuses(lambda: move_to_unit(db, world.admin_a1, world.unit_a2), status=409)
    # answered here rather than as an IntegrityError from the partial unique index
    assert "admin-a2" in error.detail


def test_a_unit_admin_moves_into_a_unit_that_has_none(world, db):
    db.user.delete(where={"id": world.admin_b1.id})
    moved = move_to_unit(db, world.admin_a1, world.unit_b1)
    assert moved.unit_id == world.unit_b1.id
    assert moved.role == Role.unit_admin


# ---------- delete_account ----------

def test_deleting_an_account_keeps_what_it_suggested(world, db):
    """The `ON DELETE SET NULL` in `prisma/migrations/0001_init`: an approved record is
    part of the dataset everyone searches and must not vanish because its author left."""
    db.jobrecord.create(data={"job_title": "راننده زره‌پوش",
                              "status": JobStatus.approved,
                              "suggested_by": world.user_a1.id})

    delete_account(db, world.user_a1)

    survivors = db.jobrecord.find_many()
    assert len(survivors) == 1
    assert survivors[0].job_title == "راننده زره‌پوش"
    assert survivors[0].suggested_by is None


# ---------- the invariants Prisma cannot declare ----------
# `ck_users_scope` and the two partial unique indexes are hand-written SQL at the foot of
# `prisma/migrations/0001_init/migration.sql`: Prisma has no syntax for a CHECK
# constraint or for a filtered index, and its introspection does not see either, so
# nothing but these tests would notice them going missing from a future migration.

def test_the_database_refuses_a_role_carrying_the_wrong_scope_column(world, db):
    """`ck_users_scope`. Checked here rather than only in `create_account`, because the
    handlers are not the only writer — the seed script and any future migration write
    rows too."""
    with pytest.raises(PrismaError):
        db.user.create(data={"username": "impossible", "hashed_password": "x",
                             "role": Role.unit_admin,
                             "organization_id": world.org_a.id,   # belongs to a unit,
                             "unit_id": world.unit_a1.id})        # never to both
    with pytest.raises(PrismaError):
        db.user.create(data={"username": "also-impossible", "hashed_password": "x",
                             "role": Role.super_admin, "unit_id": world.unit_a1.id})


def test_the_database_refuses_a_second_admin_in_one_unit(world, db):
    """`uq_users_unit_admin`, past the 409 `create_account` answers with first."""
    with pytest.raises(PrismaError):
        db.user.create(data={"username": "second-a1", "hashed_password": "x",
                             "role": Role.unit_admin, "unit_id": world.unit_a1.id})


def test_the_index_that_refuses_it_is_partial(world, db):
    """The other half of the same index: ordinary users share a `unit_id` freely. Without
    the WHERE clause the constraint above would cap a unit at one *account*."""
    db.user.create(data={"username": "user-a1c", "hashed_password": "x",
                         "role": Role.user, "unit_id": world.unit_a1.id})
    assert db.user.count(where={"unit_id": world.unit_a1.id}) == 4
