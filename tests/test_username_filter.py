"""
tests/test_username_filter.py
─────────────────────────────
Unit tests for UsernameChangeFilter (Spec v1.3).
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from security.username_filter import UsernameChangeFilter


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.anyio
async def test_username_change_from_api_metadata(async_session: AsyncSession):
    filter_svc = UsernameChangeFilter(async_session)

    # API metadata explicitly lists previous usernames
    raw = {
        "user_info": {
            "screen_name": "crypto_scam",
            "previous_usernames": ["old_dev", "rug_token"],
        }
    }
    is_excluded, reason = await filter_svc.check_account(
        platform="x",
        account_id="999888",
        current_handle="crypto_scam",
        raw_payload=raw,
    )
    assert is_excluded is True
    assert "Verified username changes in API history" in reason


@pytest.mark.anyio
async def test_username_change_from_persistent_tracking(async_session: AsyncSession):
    filter_svc = UsernameChangeFilter(async_session)

    # First encounter of account 10001 under handle 'alpha_token'
    is_excluded, _ = await filter_svc.check_account(
        platform="x",
        account_id="10001",
        current_handle="alpha_token",
    )
    assert is_excluded is False

    # Second encounter of same immutable numeric ID under changed handle 'beta_token'
    is_excluded, reason = await filter_svc.check_account(
        platform="x",
        account_id="10001",
        current_handle="beta_token",
    )
    assert is_excluded is True
    assert "Account renamed from @alpha_token to @beta_token" in reason

    # Third encounter retains exclusion status
    is_excluded, reason = await filter_svc.check_account(
        platform="x",
        account_id="10001",
        current_handle="beta_token",
    )
    assert is_excluded is True
    assert "Account has verified rename history" in reason


@pytest.mark.anyio
async def test_username_change_not_invented_when_unavailable(async_session: AsyncSession):
    filter_svc = UsernameChangeFilter(async_session)

    # Account without prior record and no API history should NOT be excluded
    is_excluded, reason = await filter_svc.check_account(
        platform="x",
        account_id=None,
        current_handle="clean_dev",
        raw_payload={},
    )
    assert is_excluded is False
    assert reason is None
