import logging

import pytest
from pytest_toolbox.comparison import AnyInt, CloseToNow

from foxglove import BaseSettings
from foxglove.db.migrations import run_migrations
from foxglove.db.patches import Patch, run_patch
from foxglove.db.utils import AsyncPgContext
from tests.conftest import SyncConnContext

pytestmark = pytest.mark.asyncio


def test_patch_live(settings: BaseSettings, wipe_db, caplog):
    caplog.set_level(logging.INFO, 'foxglove.db')
    run_patch('insert_org', True, {})
    with SyncConnContext(settings.pg_dsn) as conn:
        assert conn.fetchval('select count(*) from organisations') == 1

    assert caplog.messages == [
        '--------- running patch insert_org live ----------',
        '------------- live, committed patch --------------',
    ]


def test_patch_dry_run(settings: BaseSettings, wipe_db, caplog):
    caplog.set_level(logging.INFO)
    run_patch('insert_org', False, {})
    with SyncConnContext(settings.pg_dsn) as conn:
        assert conn.fetchval('select count(*) from organisations') == 0

    assert caplog.messages == [
        '------- running patch insert_org not live --------',
        '------------- not live, rolling back -------------',
    ]


def test_patch_error(settings: BaseSettings, wipe_db, caplog):
    caplog.set_level(logging.INFO, 'foxglove.db')
    run_patch('insert_org', False, {'fail': '1'})
    with SyncConnContext(settings.pg_dsn) as conn:
        assert conn.fetchval('select count(*) from organisations') == 0

    assert caplog.messages == [
        '------- running patch insert_org not live --------',
        '--------------------- error ----------------------',
        'Error running insert_org patch',
    ]


async def test_run_migrations_ok(settings: BaseSettings, wipe_db, db_conn, caplog):
    async def ok_patch(logger, **kwargs):
        logger.info('running ok_patch')

    patches = [Patch(ok_patch, auto_ref='foobar')]

    caplog.set_level(logging.DEBUG, 'foxglove.db')
    assert await run_migrations(settings, patches) == 1
    async with AsyncPgContext(settings.pg_dsn) as conn:
        assert await conn.fetchval("select exists (select from pg_tables where tablename='migrations')") is True
        assert await conn.fetchval('select count(*) from migrations') == 1
        migrations = dict(await conn.fetchrow('select * from migrations'))

    assert migrations == {
        'id': AnyInt(),
        'patch_name': 'ok_patch',
        'auto_ref': 'foobar',
        'sql_section_name': '-',
        'sql_section_content': '-',
        'ts': CloseToNow(),
    }
    assert await run_migrations(settings, patches) == 0

    async with AsyncPgContext(settings.pg_dsn) as conn:
        assert await conn.fetchval('select count(*) from migrations') == 1

    async with AsyncPgContext(settings.pg_dsn) as conn:
        async with conn.transaction():
            await conn.execute('lock table migrations')
            assert await run_migrations(settings, patches) == 0

    assert caplog.messages == [
        'checking 1 migration patches...',
        '---------------- running ok_patch ----------------',
        'running ok_patch',
        '--------------- ok_patch succeeded ---------------',
        '1 migration patches run, 0 already up to date ✓',
        'checking 1 migration patches...',
        '0 migration patches run, 1 already up to date ✓',
        'another transaction has locked migrations, skipping migrations here',
    ]


async def test_run_migrations_error(settings: BaseSettings, wipe_db, caplog):
    def ok_patch(logger, **kwargs):
        return 'hello'

    async def error_patch(**kwargs):
        raise ValueError('broken')

    patches = [Patch(ok_patch, auto_ref='foobar'), Patch(error_patch, auto_ref='foobar')]

    caplog.set_level(logging.INFO, 'foxglove.db')
    assert await run_migrations(settings, patches) == 0

    async with AsyncPgContext(settings.pg_dsn) as conn:
        assert await conn.fetchval("select exists (select from pg_tables where tablename='migrations')") is False

    assert caplog.messages == [
        'checking 2 migration patches...',
        '---------------- running ok_patch ----------------',
        'result: hello',
        '--------------- ok_patch succeeded ---------------',
        '-------------- running error_patch ---------------',
        '--------------- error_patch failed ---------------',
        'Error running error_patch migration patch',
        'patch failed, rolling back all 1 migration patches in this session',
    ]


async def test_run_migrations_none(settings: BaseSettings, wipe_db, caplog):
    caplog.set_level(logging.INFO, 'foxglove.db')
    assert await run_migrations(settings, []) == 0

    async with AsyncPgContext(settings.pg_dsn) as conn:
        assert await conn.fetchval("select exists (select from pg_tables where tablename='migrations')") is False

    assert caplog.messages == []
