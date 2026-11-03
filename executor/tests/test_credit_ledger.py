"""REQ-20261103-153904: actual-use billing, atomic wallets and recovery."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.platform.core.db import Database, UserRepository
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.credits.ledger import BillingLedger, BillingError


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "meta.db", load_master_key(generate_master_key()))
    users = UserRepository(db)
    owner = users.create(username="owner", password="password123")["user_id"]
    admin = users.create(username="admin", password="password123", is_super_admin=True)["user_id"]
    users.adjust_credits(owner, 100)
    ledger = BillingLedger(db)
    ledger.publish_price("model", "100", "200", "10", admin_id=admin)
    yield db, owner, admin, ledger
    db.close()


def reserve(env, key="request", budget="50", model="model"):
    _, owner, _, ledger = env
    return ledger.reserve(user_id=owner, thread_id="thread", request_id=key,
                          budget=budget, model=model, fingerprint=key)


def call(env, operation, key, *, input_tokens=1000, output_tokens=1000,
         cached_tokens=0, billable=True):
    return env[3].record_call(operation["operation_id"], key, "model",
                              input_tokens=input_tokens, output_tokens=output_tokens,
                              cached_tokens=cached_tokens, billable=billable)


def test_actual_price_cache_and_whole_operation_rounding(env):
    _, owner, admin, ledger = env
    ledger.publish_price("model", "100", "200", "10", admin_id=admin)
    operation = reserve(env)
    call(env, operation, "a", input_tokens=100000, output_tokens=100000, cached_tokens=0)
    call(env, operation, "b", input_tokens=100000, output_tokens=0, cached_tokens=100000)
    result = ledger.settle(operation["operation_id"], "completed")
    assert result["charged"] == 31.0
    assert ledger.balance(owner)["balance"] == 69.0
    operation = reserve(env, "tiny")
    call(env, operation, "c", input_tokens=40, output_tokens=0)
    call(env, operation, "d", input_tokens=40, output_tokens=0)
    assert ledger.settle(operation["operation_id"], "completed")["charged"] == 0.01


def test_reservation_is_not_a_charge_and_stop_releases_remainder(env):
    _, owner, admin, ledger = env
    ledger.publish_price("model", "100", "0", "0", admin_id=admin)
    operation = reserve(env)
    assert ledger.balance(owner) == {"balance": 50.0, "total_balance": 100.0, "reserved": 50.0}
    call(env, operation, "used", input_tokens=123400, output_tokens=0)
    assert ledger.settle(operation["operation_id"], "stopped")["charged"] == 12.34
    assert ledger.balance(owner) == {"balance": 87.66, "total_balance": 87.66, "reserved": 0.0}


def test_failure_refunds_whole_operation_and_retry_cost_is_platforms(env):
    operation = reserve(env)
    call(env, operation, "normal")
    call(env, operation, "retry", billable=False)
    result = env[3].settle(operation["operation_id"], "completed")
    assert result["charged"] == 0.3
    failed = reserve(env, "failure")
    call(env, failed, "normal")
    assert env[3].settle(failed["operation_id"], "failed")["charged"] == 0
    assert env[3].balance(env[1])["balance"] == 99.7


def test_budget_caps_charge_and_never_creates_debt(env):
    _, owner, admin, ledger = env
    ledger.publish_price("model", "100", "0", "0", admin_id=admin)
    operation = reserve(env, budget="10")
    call(env, operation, "large", input_tokens=120000, output_tokens=0)
    assert ledger.exhausted(operation["operation_id"])
    assert ledger.settle(operation["operation_id"], "budget_exhausted")["charged"] == 10
    assert ledger.balance(owner)["balance"] == 90
    for budget in ("0", "-1", "91", "NaN", "0.001"):
        with pytest.raises(BillingError):
            reserve(env, budget, budget)


def test_lock_price_and_unpriced_models_fail_before_reserving(env):
    _, owner, admin, ledger = env
    operation = reserve(env)
    ledger.publish_price("model", "200", "400", "20", admin_id=admin)
    call(env, operation, "old", input_tokens=1000, output_tokens=0)
    assert ledger.settle(operation["operation_id"], "completed")["charged"] == 0.1
    fresh = reserve(env, "fresh")
    call(env, fresh, "new", input_tokens=1000, output_tokens=0)
    assert ledger.settle(fresh["operation_id"], "completed")["charged"] == 0.2
    before = ledger.balance(owner)
    with pytest.raises(BillingError):
        reserve(env, "missing", model="missing")
    assert ledger.balance(owner) == before


def test_unknown_usage_not_estimated_and_duplicate_calls_not_counted(env):
    operation = reserve(env)
    call(env, operation, "known")
    call(env, operation, "known")
    call(env, operation, "unknown", output_tokens=None)
    result = env[3].settle(operation["operation_id"], "completed")
    assert result["charged"] == 0.3
    assert result["missing_usage"] == 1
    assert len(env[3].operation(operation["operation_id"], env[1])["calls"]) == 2


def test_concurrent_reservations_and_idempotent_settlement(env):
    ledger = env[3]
    def run(key):
        try:
            return reserve(env, key, "80")
        except BillingError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["one", "two"]))
    operation = next(row for row in results if row)
    assert sum(row is not None for row in results) == 1
    assert reserve(env, operation["request_id"], "80")["operation_id"] == operation["operation_id"]
    call(env, operation, "used")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: ledger.settle(operation["operation_id"], "completed"), range(2)))
    assert ledger.balance(env[1])["balance"] == 99.7


def test_admin_adjustment_is_atomic_audited_and_respects_holds(env):
    _, owner, admin, ledger = env
    reserve(env, budget="80")
    for _ in range(2):
        ledger.adjust(owner, "-20", "纠错", admin_id=admin, request_id="same")
    assert ledger.balance(owner)["balance"] == 0
    with pytest.raises(BillingError):
        ledger.adjust(owner, "-0.01", "纠错", admin_id=admin, request_id="extra")
    with pytest.raises(BillingError):
        ledger.adjust(owner, "1", "", admin_id=admin, request_id="blank")
    with pytest.raises(BillingError):
        ledger.adjust(owner, "1", "无权", admin_id=owner, request_id="forbidden")


def test_migration_preserves_positive_balances_and_clears_debt_once(tmp_path):
    db = Database(tmp_path / "legacy.db", load_master_key(generate_master_key()))
    users = UserRepository(db)
    ids = [users.create(username=f"u{i}", password="password123")["user_id"] for i in range(3)]
    for owner, amount in zip(ids, (100, 0, -50)):
        users.adjust_credits(owner, amount)
    for _ in range(2):
        ledger = BillingLedger(db)
        assert [ledger.balance(owner)["balance"] for owner in ids] == [100, 0, 0]
    assert len([row for row in ledger.transactions(ids[2]) if row["type"] == "migration_adjust"]) == 1
    db.close()


def test_settlement_receipt_survives_failure_and_restart(env, monkeypatch):
    _, owner, _, ledger = env
    operation = reserve(env)
    call(env, operation, "used")
    original = ledger.settle
    monkeypatch.setattr(ledger, "settle", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    assert ledger.finish(operation["operation_id"], "completed") is None
    assert ledger.operation(operation["operation_id"], owner)["status"] == "processing"
    monkeypatch.setattr(ledger, "settle", original)
    ledger.recover(set())
    assert ledger.balance(owner)["balance"] == 99.7
    ledger.recover(set())
    assert ledger.balance(owner)["balance"] == 99.7


def test_orphan_is_refunded_and_live_operation_is_preserved(env):
    operation = reserve(env)
    call(env, operation, "used")
    env[3].recover({operation["operation_id"]})
    assert env[3].balance(env[1])["reserved"] == 50
    env[3].recover(set())
    assert env[3].balance(env[1])["balance"] == 100


def test_invite_creation_and_registration_are_atomic_and_idempotent(env, monkeypatch):
    db, _, admin, ledger = env
    codes = ledger.create_invites(admin_id=admin, count=1, amount="12.34", note="体验发放", request_id="batch")
    assert ledger.create_invites(admin_id=admin, count=1, amount="12.34", note="体验发放", request_id="batch") == codes
    original = ledger._entry
    monkeypatch.setattr(ledger, "_entry", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("storage failed")))
    with pytest.raises(OSError):
        ledger.register_invited(username="new", password="password123", code=codes[0], quota=5)
    assert UserRepository(db).get_by_username("new") is None
    monkeypatch.setattr(ledger, "_entry", original)
    user = ledger.register_invited(username="new", password="password123", code=codes[0], quota=5)
    assert ledger.balance(user["user_id"])["balance"] == 12.34
    ledger.recover(set())
    assert ledger.balance(user["user_id"])["balance"] == 12.34
    with pytest.raises(BillingError):
        ledger.register_invited(username="another", password="password123", code=codes[0], quota=5)
    assert UserRepository(db).get_by_username("another") is None


def test_saved_final_result_is_settled_after_restart(env):
    ledger = env[3]
    operation = reserve(env)
    call(env, operation, "used")
    ledger.save_result(operation["operation_id"], {"content": "saved"})
    ledger.recover(set())
    result = ledger.operation(operation["operation_id"], env[1])
    assert result["status"] == "completed"
    assert result["charged"] == 0.3


def test_corrupt_receipt_does_not_block_other_recovery(env):
    operation = reserve(env)
    (env[3].receipts / "broken.json").write_text("{", encoding="utf-8")
    env[3].recover(set())
    assert env[3].operation(operation["operation_id"], env[1])["status"] == "failed"
