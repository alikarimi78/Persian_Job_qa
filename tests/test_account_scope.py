import pytest
from fastapi import HTTPException
from prisma.errors import PrismaError

from src.accounts import (assert_can_manage_account, assert_manages_organization,
                          create_account, delete_account, move_to_organization,
                          visible_users)
from src.models import JobStatus, Role

PASSWORD = "Password-1"


def refuses(fn, *args, status=403):
    with pytest.raises(HTTPException) as raised:
        fn(*args)
    assert raised.value.status_code == status
    return raised.value


def test_a_super_admin_manages_every_organization(world):
    assert_manages_organization(world.root, world.org_a.id)
    assert_manages_organization(world.root, world.org_b.id)


def test_an_org_admin_manages_only_its_own(world):
    assert_manages_organization(world.admin_a, world.org_a.id)
    refuses(assert_manages_organization, world.admin_a, world.org_b.id)


def test_an_ordinary_user_manages_no_organization(world):
    refuses(assert_manages_organization, world.user_a1, world.org_a.id)


@pytest.mark.parametrize("account", ["root", "admin_a", "user_a1"])
def test_nobody_may_act_on_their_own_account(world, account):
    actor = getattr(world, account)
    refuses(assert_can_manage_account, actor, actor)


@pytest.mark.parametrize("target", ["admin_a", "user_a1", "user_b1"])
def test_a_super_admin_may_act_on_anyone_else(world, target):
    assert_can_manage_account(world.root, getattr(world, target))


def test_an_org_admin_reaches_the_users_of_its_organization(world):
    assert_can_manage_account(world.admin_a, world.user_a1)
    assert_can_manage_account(world.admin_a, world.user_a2)


def test_an_org_admin_reaches_nothing_outside_it(world):
    refuses(assert_can_manage_account, world.admin_a, world.user_b1)


def test_an_org_admin_may_not_act_on_a_peer_or_on_a_super_admin(world):
    refuses(assert_can_manage_account, world.admin_a, world.admin_b)
    refuses(assert_can_manage_account, world.admin_a, world.root)


def test_an_ordinary_user_may_act_on_nobody(world):
    refuses(assert_can_manage_account, world.user_a1, world.user_a2)


def test_a_super_admin_sees_every_account(world, db):
    assert len(visible_users(db, world.root)) == db.user.count()


def test_an_org_admin_sees_the_users_of_its_organization(world, db):
    seen = {u.username for u in visible_users(db, world.admin_a)}
    assert seen == {"user-a1", "user-a2"}
    assert "admin-a" not in seen


def test_usernames_are_global_not_per_tenant(world, db):
    refuses(lambda: create_account(db, username="user-a1", password=PASSWORD,
                                   role=Role.user,
                                   organization_id=world.org_b.id), status=409)


def test_an_organization_holds_one_admin(world, db):
    error = refuses(lambda: create_account(db, username="second-a", password=PASSWORD,
                                           role=Role.org_admin,
                                           organization_id=world.org_a.id), status=409)
    assert "admin-a" in error.detail


def test_an_organization_holds_as_many_ordinary_users_as_it_likes(world, db):
    made = create_account(db, username="user-a3", password=PASSWORD, role=Role.user,
                          organization_id=world.org_a.id)
    assert made.organization_id == world.org_a.id
    assert made.role == Role.user


def test_a_created_account_carries_exactly_its_own_scope_column(world, db):
    org_c = db.organization.create(data={"name": "org-c"})

    org_admin = create_account(db, username="admin-c", password=PASSWORD,
                               role=Role.org_admin, organization_id=org_c.id)
    assert org_admin.organization_id == org_c.id

    root = create_account(db, username="root-2", password=PASSWORD,
                          role=Role.super_admin)
    assert root.organization_id is None


def test_a_user_moves_and_keeps_its_role(world, db):
    moved = move_to_organization(db, world.user_a1, world.org_b)
    assert moved.organization_id == world.org_b.id
    assert moved.role == Role.user


def test_an_account_that_lives_in_no_organization_cannot_move(world, db):
    refuses(lambda: move_to_organization(db, world.root, world.org_a), status=409)


def test_an_org_admin_may_not_land_on_a_sitting_admin(world, db):
    error = refuses(lambda: move_to_organization(db, world.admin_a, world.org_b),
                    status=409)
    assert "admin-b" in error.detail


def test_an_org_admin_moves_into_an_organization_that_has_none(world, db):
    db.user.delete(where={"id": world.admin_b.id})
    moved = move_to_organization(db, world.admin_a, world.org_b)
    assert moved.organization_id == world.org_b.id
    assert moved.role == Role.org_admin


def test_a_user_moving_into_an_organization_that_has_an_admin_is_fine(world, db):
    moved = move_to_organization(db, world.user_b1, world.org_a)
    assert moved.organization_id == world.org_a.id


def test_deleting_an_account_keeps_what_it_suggested(world, db):
    db.jobrecord.create(data={"job_title": "راننده زره‌پوش",
                              "status": JobStatus.approved,
                              "suggested_by": world.user_a1.id})

    delete_account(db, world.user_a1)

    survivors = db.jobrecord.find_many()
    assert len(survivors) == 1
    assert survivors[0].job_title == "راننده زره‌پوش"
    assert survivors[0].suggested_by is None


def test_the_database_refuses_a_role_carrying_the_wrong_scope_column(world, db):
    with pytest.raises(PrismaError):
        db.user.create(data={"username": "impossible", "hashed_password": "x",
                             "role": Role.super_admin,
                             "organization_id": world.org_a.id})
    with pytest.raises(PrismaError):
        db.user.create(data={"username": "also-impossible", "hashed_password": "x",
                             "role": Role.org_admin})


def test_the_database_refuses_a_second_admin_in_one_organization(world, db):
    with pytest.raises(PrismaError):
        db.user.create(data={"username": "second-a", "hashed_password": "x",
                             "role": Role.org_admin,
                             "organization_id": world.org_a.id})


def test_the_index_that_refuses_it_is_partial(world, db):
    db.user.create(data={"username": "user-a3", "hashed_password": "x",
                         "role": Role.user, "organization_id": world.org_a.id})
    assert db.user.count(where={"organization_id": world.org_a.id}) == 4
