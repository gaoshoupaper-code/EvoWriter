"""Authoritative v2 wallets. Amounts are centipoints; call costs stay exact until settlement."""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from app.platform.core.db import Database, _now

logger = logging.getLogger(__name__)
FINAL = {"completed", "stopped", "budget_exhausted", "failed"}
SETTLEMENT_NOTES = {
    "completed": "正常完成，按实际用量结算",
    "stopped": "主动停止，按已用量结算",
    "budget_exhausted": "预算用尽，按已用量结算",
    "failed": "系统故障，本笔积分全部退回",
}


class BillingError(ValueError):
    def __init__(self, message: str, code: str = "billing_invalid", status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def decimal(value) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise BillingError("积分或系数必须是有限数字") from None


def cents(value, *, positive: bool = False) -> int:
    number = decimal(value) * 100
    if number != number.to_integral_value() or abs(number) > 9_000_000_000_000_000:
        raise BillingError("积分最多保留两位小数，且不得超过支持范围")
    if positive and number <= 0:
        raise BillingError("本次预算必须大于零")
    return int(number)


def model_key(model: str) -> str:
    return model.split(":", 1)[-1].strip()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_wallets (
 user_id TEXT PRIMARY KEY REFERENCES users(user_id), balance INTEGER NOT NULL CHECK(balance >= 0)
);
CREATE TABLE IF NOT EXISTS billing_prices (
 model TEXT PRIMARY KEY, input_per_million TEXT NOT NULL, output_per_million TEXT NOT NULL,
 cached_per_million TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_operations (
 operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
 thread_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 budget INTEGER NOT NULL, prices TEXT NOT NULL, status TEXT NOT NULL,
 consumed TEXT NOT NULL DEFAULT '0', charged INTEGER NOT NULL DEFAULT 0,
 missing_usage INTEGER NOT NULL DEFAULT 0, trace_id TEXT, created_at TEXT NOT NULL,
 settled_at TEXT, UNIQUE(user_id, request_id)
);
CREATE INDEX IF NOT EXISTS billing_operations_user ON billing_operations(user_id, created_at);
CREATE TABLE IF NOT EXISTS billing_calls (
 operation_id TEXT NOT NULL REFERENCES billing_operations(operation_id), call_id TEXT NOT NULL,
 model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
 cost TEXT NOT NULL, platform_cost TEXT, billable INTEGER NOT NULL, usage_known INTEGER NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(operation_id, call_id)
);
CREATE TABLE IF NOT EXISTS billing_entries (
 entry_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), type TEXT NOT NULL,
 amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, operation_id TEXT,
 note TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_admin_requests (
 request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_invite_grants (
 code TEXT PRIMARY KEY REFERENCES invite_codes(code), amount INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_results (
 operation_id TEXT PRIMARY KEY REFERENCES billing_operations(operation_id), result TEXT NOT NULL,
 event_type TEXT NOT NULL DEFAULT 'final'
);
CREATE TABLE IF NOT EXISTS billing_finalizations (
 operation_id TEXT PRIMARY KEY REFERENCES billing_operations(operation_id), outcome TEXT NOT NULL
);
"""


class BillingLedger:
    def __init__(self, db: Database):
        self.db = db
        self.receipts = db.db_path.parent / "billing_receipts"
        self.receipts.mkdir(parents=True, exist_ok=True)
        with db._lock:
            db.conn.executescript(_SCHEMA)
            if "event_type" not in {row["name"] for row in db.conn.execute("PRAGMA table_info(billing_results)")}:
                db.conn.execute("ALTER TABLE billing_results ADD COLUMN event_type TEXT NOT NULL DEFAULT 'final'")
        with db.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO credit_config(key,value,description,updated_at) VALUES (?,?,?,?)",
                         ("billing_migrated_at", _now(), "积分 v2 迁移分界", _now()))
            for user in conn.execute("SELECT user_id FROM users").fetchall():
                self._wallet(conn, user["user_id"])

    def _wallet(self, conn, user_id):
        row = conn.execute("SELECT balance FROM billing_wallets WHERE user_id=?", (user_id,)).fetchone()
        if row is not None:
            return row["balance"]
        user = conn.execute("SELECT credits_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if user is None:
            raise BillingError("用户不存在", "user_not_found", 404)
        old = cents(user["credits_balance"])
        balance = max(0, old)
        conn.execute("INSERT INTO billing_wallets VALUES (?,?)", (user_id, balance))
        if old < 0:
            self._entry(conn, user_id, "migration_adjust", -old, 0,
                        f"旧负余额 {old / 100:.2f} 归零，保留历史流水", "system",
                        f"migration:{user_id}", "migration")
        return balance

    @staticmethod
    def _reserved(conn, user_id):
        return conn.execute(
            "SELECT COALESCE(SUM(budget),0) FROM billing_operations "
            "WHERE user_id=? AND status NOT IN ('completed','stopped','budget_exhausted','failed')",
            (user_id,),
        ).fetchone()[0]

    def balance(self, user_id) -> dict:
        with self.db.transaction() as conn:
            total = self._wallet(conn, user_id)
            reserved = self._reserved(conn, user_id)
            return {"balance": (total - reserved) / 100, "total_balance": total / 100,
                    "reserved": reserved / 100}

    @staticmethod
    def _admin(conn, admin_id):
        user = conn.execute("SELECT is_super_admin FROM users WHERE user_id=?", (admin_id,)).fetchone()
        if user is None or not user["is_super_admin"]:
            raise BillingError("需要超级管理员权限", "forbidden", 403)

    def publish_price(self, model, input_per_million, output_per_million,
                      cached_per_million, *, admin_id):
        key = model_key(model)
        values = [decimal(value) for value in (input_per_million, output_per_million, cached_per_million)]
        if not key or len(key) > 200 or any(value < 0 or value > Decimal("1000000000") for value in values):
            raise BillingError("模型名称或积分系数无效")
        with self.db.transaction() as conn:
            self._admin(conn, admin_id)
            conn.execute(
                "INSERT INTO billing_prices VALUES (?,?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET "
                "input_per_million=excluded.input_per_million, output_per_million=excluded.output_per_million, "
                "cached_per_million=excluded.cached_per_million, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (key, *(str(value) for value in values), admin_id, _now()),
            )
        return next(row for row in self.prices() if row["model"] == key)

    def prices(self):
        with self.db._lock:
            return [dict(row) for row in self.db.conn.execute(
                "SELECT model,input_per_million,output_per_million,cached_per_million,updated_at "
                "FROM billing_prices ORDER BY model")]

    def config(self):
        with self.db._lock:
            rows = dict(self.db.conn.execute(
                "SELECT key,value FROM credit_config WHERE key IN ('billing_enabled','billing_default_budgets')"))
        return {"enabled": rows.get("billing_enabled") == "true",
                "default_budgets": json.loads(rows.get("billing_default_budgets", "{}")),
                "coefficient_unit": "credits_per_million_tokens", "contact_qq": "1134559830", "protocol": 2}

    def configure(self, *, enabled: bool, default_budgets: dict, admin_id: str):
        if not isinstance(enabled, bool) or not default_budgets:
            raise BillingError("请提供启用状态和基于历史用量的默认预算")
        budgets = {str(key): cents(value, positive=True) / 100 for key, value in default_budgets.items()}
        with self.db.transaction() as conn:
            self._admin(conn, admin_id)
            if enabled and conn.execute("SELECT COUNT(*) FROM billing_prices").fetchone()[0] == 0:
                raise BillingError("请先配置有效模型积分系数", "model_unpriced", 409)
            for key, value in {"billing_enabled": json.dumps(enabled),
                               "billing_default_budgets": json.dumps(budgets)}.items():
                conn.execute("INSERT INTO credit_config(key,value,description,updated_at) VALUES (?,?,?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                             (key, value, "积分计费 v2", _now()))
        return self.config()

    def reserve(self, *, user_id, thread_id, request_id, budget, model, fingerprint):
        amount = cents(budget, positive=True)
        if not request_id or len(request_id) > 200:
            raise BillingError("请升级客户端并提交稳定的操作标识", "billing_upgrade_required", 426)
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM billing_operations WHERE user_id=? AND request_id=?",
                                    (user_id, request_id)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint or existing["budget"] != amount:
                    raise BillingError("同一操作标识不能提交不同内容或预算", "request_conflict", 409)
                return self._public(existing)
            prices = {row["model"]: dict(row) for row in conn.execute("SELECT * FROM billing_prices")}
            if model_key(model) not in prices:
                raise BillingError("当前模型未配置有效积分系数，请联系管理员", "model_unpriced", 409)
            balance = self._wallet(conn, user_id)
            if balance - self._reserved(conn, user_id) < amount:
                raise BillingError("可用积分不足，请降低本次预算或加 QQ 群 1134559830 补分", "insufficient_credits", 402)
            operation_id = uuid4().hex
            conn.execute("INSERT INTO billing_operations(operation_id,user_id,thread_id,request_id,fingerprint,"
                         "budget,prices,status,created_at) VALUES (?,?,?,?,?,?,?,'reserved',?)",
                         (operation_id, user_id, thread_id, request_id, fingerprint, amount, json.dumps(prices), _now()))
            return self._public(conn.execute("SELECT * FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone())

    @staticmethod
    def _public(row):
        result = dict(row)
        result["budget"] /= 100
        result["charged"] /= 100
        result["consumed"] = float(result["consumed"])
        result.pop("fingerprint", None)
        result.pop("prices", None)
        return result

    def operation(self, operation_id, user_id=None):
        with self.db._lock:
            row = self.db.conn.execute("SELECT * FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None or (user_id is not None and row["user_id"] != user_id):
                raise BillingError("操作不存在", "operation_not_found", 404)
            result = self._public(row)
            result["prices"] = json.loads(row["prices"])
            # Never disclose administrator identity as part of the public price snapshot.
            for price in result["prices"].values():
                price.pop("updated_by", None)
            result["calls"] = [dict(call) for call in self.db.conn.execute(
                "SELECT * FROM billing_calls WHERE operation_id=? ORDER BY created_at,call_id", (operation_id,))]
            saved = self.db.conn.execute("SELECT result,event_type FROM billing_results WHERE operation_id=?", (operation_id,)).fetchone()
            result["result"] = json.loads(saved["result"]) if saved else None
            result["result_event"] = saved["event_type"] if saved else None
            return result

    def save_result(self, operation_id, result, event_type="final"):
        if event_type not in {"final", "interrupt"}:
            raise BillingError("无效操作结果类型")
        with self.db.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO billing_results(operation_id,result,event_type) VALUES (?,?,?)",
                         (operation_id, json.dumps(result, ensure_ascii=False), event_type))
            self._finalization(conn, operation_id, "completed")

    def find_request(self, user_id, request_id):
        with self.db._lock:
            row = self.db.conn.execute("SELECT * FROM billing_operations WHERE user_id=? AND request_id=?",
                                       (user_id, request_id)).fetchone()
            return self._public(row) if row else None

    def can_continue(self, operation_id):
        """A saved settlement intent closes execution before its ledger replay."""
        receipt = self.receipts / f"{operation_id}.json"
        if receipt.exists():
            return False
        with self.db._lock:
            row = self.db.conn.execute("SELECT status FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone()
            pending = self.db.conn.execute("SELECT 1 FROM billing_finalizations WHERE operation_id=?", (operation_id,)).fetchone()
            return bool(row and row["status"] in {"reserved", "running"} and not pending and not receipt.exists())

    @staticmethod
    def _finalization(conn, operation_id, outcome):
        if outcome not in FINAL:
            raise BillingError("无效结算原因")
        conn.execute("INSERT INTO billing_finalizations VALUES (?,?) ON CONFLICT(operation_id) "
                     "DO UPDATE SET outcome=excluded.outcome", (operation_id, outcome))
        conn.execute("UPDATE billing_operations SET status='processing' WHERE operation_id=? "
                     "AND status NOT IN ('completed','stopped','budget_exhausted','failed')", (operation_id,))

    def operations(self, user_id=None, limit=50):
        with self.db._lock:
            rows = self.db.conn.execute(
                "SELECT * FROM billing_operations " + ("WHERE user_id=? " if user_id else "") +
                "ORDER BY created_at DESC LIMIT ?", ((user_id, limit) if user_id else (limit,))).fetchall()
            return [self._public(row) for row in rows]

    def started(self, operation_id, trace_id=None):
        with self.db.transaction() as conn:
            conn.execute("UPDATE billing_operations SET status='running',trace_id=COALESCE(?,trace_id) "
                         "WHERE operation_id=? AND status IN ('reserved','running')", (trace_id, operation_id))

    def record_call(self, operation_id, call_id, model, *, input_tokens, output_tokens,
                    cached_tokens=0, billable=True):
        def token(value):
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0
        known = all(token(value) for value in (input_tokens, output_tokens, cached_tokens))
        known = known and cached_tokens <= input_tokens
        with self.db.transaction() as conn:
            operation = conn.execute("SELECT * FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if operation is None or operation["status"] in FINAL:
                return
            prices = json.loads(operation["prices"])
            price = prices.get(model_key(model))
            if price is None:
                raise BillingError("操作中的模型没有锁定积分系数", "model_unpriced", 409)
            cost = Decimal(0)
            if known:
                cost = ((input_tokens - cached_tokens) * decimal(price["input_per_million"]) +
                        cached_tokens * decimal(price["cached_per_million"]) +
                        output_tokens * decimal(price["output_per_million"])) / 1_000_000
            conn.execute("INSERT OR IGNORE INTO billing_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (operation_id, call_id, model_key(model), input_tokens, output_tokens, cached_tokens,
                          str(cost if billable else 0), str(cost) if known else None,
                          int(billable), int(known), _now()))
            calls = conn.execute("SELECT cost,usage_known FROM billing_calls WHERE operation_id=?", (operation_id,)).fetchall()
            total = sum((decimal(row["cost"]) for row in calls), Decimal(0))
            conn.execute("UPDATE billing_operations SET consumed=?,missing_usage=? WHERE operation_id=?",
                         (str(total), sum(not row["usage_known"] for row in calls), operation_id))

    def exhausted(self, operation_id):
        with self.db._lock:
            row = self.db.conn.execute("SELECT budget,consumed FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone()
            return row is not None and decimal(row["consumed"]) * 100 >= row["budget"]

    @staticmethod
    def _entry(conn, user_id, kind, amount, balance, note, actor, request_id, fingerprint, operation_id=None):
        conn.execute("INSERT INTO billing_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (uuid4().hex, user_id, kind, amount, balance, operation_id, note, actor,
                      _now(), request_id, fingerprint))

    def settle(self, operation_id, outcome):
        if outcome not in FINAL:
            raise BillingError("无效结算原因")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise BillingError("操作不存在", "operation_not_found", 404)
            if row["status"] not in FINAL:
                charge = 0 if outcome == "failed" else min(
                    row["budget"], int((decimal(row["consumed"]) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
                total = self._wallet(conn, row["user_id"]) - charge
                conn.execute("UPDATE billing_wallets SET balance=? WHERE user_id=?", (total, row["user_id"]))
                self._entry(conn, row["user_id"], "creation_refund" if outcome == "failed" else "creation_consume",
                            -charge, total, SETTLEMENT_NOTES[outcome], "system", f"settle:{operation_id}", outcome, operation_id)
                conn.execute("UPDATE billing_operations SET status=?,charged=?,settled_at=? WHERE operation_id=?",
                             (outcome, charge, _now(), operation_id))
            result = self._public(conn.execute("SELECT * FROM billing_operations WHERE operation_id=?", (operation_id,)).fetchone())
            conn.execute("DELETE FROM billing_finalizations WHERE operation_id=?", (operation_id,))
        return result

    def finish(self, operation_id, outcome):
        # Write the desired outcome before touching the ledger; a failed DB write must not lose a completed result.
        receipt = self.receipts / f"{operation_id}.json"
        temp = receipt.with_suffix(".tmp")
        try:
            import os
            with temp.open("w", encoding="utf-8") as stream:
                json.dump({"operation_id": operation_id, "outcome": outcome}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(receipt)
        except OSError:
            logger.exception("结算文件凭据暂不可用，将尝试数据库结算 operation=%s", operation_id)
        try:
            with self.db.transaction() as conn:
                self._finalization(conn, operation_id, outcome)
            result = self.settle(operation_id, outcome)
        except Exception:
            logger.exception("结算待恢复 operation=%s", operation_id)
            return None
        receipt.unlink(missing_ok=True)
        return result

    def recover(self, active: set[str]):
        import re
        receipts_pending = set()
        for receipt in self.receipts.glob("*.json"):
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
                key, outcome = data["operation_id"], data["outcome"]
                if not re.fullmatch(r"[a-f0-9]{32}", key) or receipt.stem != key or outcome not in FINAL:
                    raise ValueError("invalid receipt")
                receipts_pending.add(key)
                self.settle(key, outcome)
                receipt.unlink(missing_ok=True)
                receipts_pending.discard(key)
            except (ValueError, KeyError, TypeError, BillingError):
                logger.exception("隔离无效结算凭据 file=%s", receipt.name)
                receipt.replace(receipt.with_suffix(".invalid"))
            except OSError:
                logger.exception("结算凭据恢复暂失败 file=%s", receipt.name)
        with self.db._lock:
            intents = [dict(row) for row in self.db.conn.execute("SELECT * FROM billing_finalizations")]
        intents_pending = {row["operation_id"] for row in intents}
        for intent in intents:
            try:
                self.settle(intent["operation_id"], intent["outcome"])
                intents_pending.discard(intent["operation_id"])
            except Exception:
                logger.exception("持久化结算恢复暂失败 operation=%s", intent["operation_id"])
        with self.db._lock:
            pending = [row[0] for row in self.db.conn.execute(
                "SELECT operation_id FROM billing_operations WHERE status NOT IN ('completed','stopped','budget_exhausted','failed')")]
        for operation_id in pending:
            if operation_id not in active and operation_id not in receipts_pending and operation_id not in intents_pending:
                self.finish(operation_id, "failed")
        self.recover_invites()

    def adjust(self, user_id, amount, note, *, admin_id, request_id):
        change = cents(amount)
        note = note.strip()
        if not change or not note or len(note) > 200 or not request_id:
            raise BillingError("请输入非零调整积分、原因和操作标识")
        fingerprint = json.dumps([user_id, change, note, admin_id])
        with self.db.transaction() as conn:
            self._admin(conn, admin_id)
            previous = conn.execute("SELECT * FROM billing_entries WHERE request_id=?", (f"adjust:{request_id}",)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise BillingError("操作标识已用于其他调整", "request_conflict", 409)
                return previous["balance_after"] / 100
            total = self._wallet(conn, user_id)
            if total + change - self._reserved(conn, user_id) < 0:
                raise BillingError("减分不能动用预留积分或产生负余额", "insufficient_credits", 409)
            conn.execute("UPDATE billing_wallets SET balance=balance+? WHERE user_id=?", (change, user_id))
            self._entry(conn, user_id, "admin_adjust", change, total + change, note, admin_id,
                        f"adjust:{request_id}", fingerprint)
            return (total + change) / 100

    def create_invites(self, *, admin_id, count, amount, note, request_id):
        import secrets
        grant = cents(amount)
        if not isinstance(count, int) or not 1 <= count <= 50 or grant < 0 or not note.strip() or not request_id:
            raise BillingError("邀请码数量、发放额度、原因或操作标识无效")
        fingerprint = json.dumps([admin_id, count, grant, note.strip()])
        with self.db.transaction() as conn:
            self._admin(conn, admin_id)
            key = f"invites:{request_id}"
            prior = conn.execute("SELECT * FROM billing_admin_requests WHERE request_id=?", (key,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise BillingError("操作标识已用于其他邀请码发放", "request_conflict", 409)
                return json.loads(prior["result"])
            codes = [secrets.token_urlsafe(16) for _ in range(count)]
            for code in codes:
                conn.execute("INSERT INTO invite_codes(code,created_by,is_admin_code,granted_credits,created_at) "
                             "VALUES (?,?,0,?,?)", (code, admin_id, grant / 100, _now()))
                conn.execute("INSERT INTO billing_invite_grants VALUES (?,?)", (code, grant))
            conn.execute("INSERT INTO billing_admin_requests VALUES (?,?,?)", (key, fingerprint, json.dumps(codes)))
            return codes

    def register_invited(self, *, username, password, code, quota):
        from app.platform.core.security import hash_password
        password_hash = hash_password(password)
        user_id, now = uuid4().hex, _now()
        with self.db.transaction() as conn:
            invite = conn.execute("SELECT * FROM invite_codes WHERE code=? AND used_by IS NULL AND revoked_at IS NULL", (code,)).fetchone()
            if invite is None:
                raise BillingError("邀请码无效或已被使用", "invite_invalid", 400)
            if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise BillingError("用户名已被占用", "username_taken", 400)
            conn.execute("INSERT INTO users(user_id,username,password_hash,is_admin,workspace_quota,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?)", (user_id, username, password_hash, invite["is_admin_code"], quota, now, now))
            conn.execute("UPDATE invite_codes SET used_by=?,used_at=? WHERE code=?", (user_id, now, code))
            self._wallet(conn, user_id)
            self._grant_invite(conn, user_id, code)
        return {"user_id": user_id, "username": username, "is_admin": bool(invite["is_admin_code"])}

    def _grant_invite(self, conn, user_id, code):
        row = conn.execute("SELECT * FROM invite_codes WHERE code=? AND used_by=?", (code, user_id)).fetchone()
        if row is None:
            return
        precise = conn.execute("SELECT amount FROM billing_invite_grants WHERE code=?", (code,)).fetchone()
        amount = precise["amount"] if precise else cents(row["granted_credits"])
        key = f"invite:{code}"
        if amount <= 0 or conn.execute("SELECT 1 FROM billing_entries WHERE request_id=?", (key,)).fetchone():
            return
        if conn.execute("SELECT 1 FROM credit_transactions WHERE user_id=? AND type='invite_grant' AND note=?",
                        (user_id, f"邀请码到账：{code}")).fetchone():
            return
        total = self._wallet(conn, user_id) + amount
        conn.execute("UPDATE billing_wallets SET balance=? WHERE user_id=?", (total, user_id))
        self._entry(conn, user_id, "invite_grant", amount, total, f"邀请码到账：{code}", "system", key, code)

    def recover_invites(self):
        with self.db._lock:
            rows = self.db.conn.execute("SELECT code,used_by FROM invite_codes WHERE used_by IS NOT NULL AND granted_credits>0 "
                                        "AND used_at >= (SELECT value FROM credit_config WHERE key='billing_migrated_at')").fetchall()
        for row in rows:
            with self.db.transaction() as conn:
                self._grant_invite(conn, row["used_by"], row["code"])

    def transactions(self, user_id=None, limit=100):
        with self.db._lock:
            params = (user_id, limit) if user_id else (limit,)
            where = "WHERE user_id=? " if user_id else ""
            result = []
            for row in self.db.conn.execute("SELECT * FROM billing_entries " + where + "ORDER BY created_at DESC LIMIT ?", params):
                value = dict(row)
                value["tx_id"] = value.pop("entry_id")
                value["amount"] /= 100
                value["balance_after"] /= 100
                value.pop("fingerprint")
                result.append(value)
            for row in self.db.conn.execute("SELECT * FROM credit_transactions " + where + "ORDER BY created_at DESC LIMIT ?", params):
                result.append({**dict(row), "legacy": True})
            return sorted(result, key=lambda row: row["created_at"], reverse=True)[:limit]


_ledger: BillingLedger | None = None


def init_billing(ledger: BillingLedger):
    global _ledger
    _ledger = ledger


def get_billing() -> BillingLedger:
    if _ledger is None:
        raise RuntimeError("Billing ledger is not initialized")
    return _ledger
