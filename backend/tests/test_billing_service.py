"""app/services/billing.py: заморозка, списание по факту, возврат — с журналом и защитой от повторов."""
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AILog, Transaction, TransactionType
from app.services import billing
from tests.test_ai_copy import assert_ledger_matches, balance_of, held_of, user_with_balance

USAGE = {"model": "gemini-test", "prompt_tokens": 600, "completion_tokens": 400, "total_tokens": 1000,
         "cost": Decimal("0.004")}


def test_hold_moves_to_held(db):
    user, _ = user_with_balance(db, "10")
    hold = billing.hold_user_balance(db, user.id)
    assert hold.amount == Decimal("0.03")
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("9.97"), Decimal("0.03"))
    tx = db.get(Transaction, hold.transaction_id)
    assert tx.type == TransactionType.AI_SPEND and tx.amount == Decimal("0.03")


def test_hold_custom_amount(db):
    user, _ = user_with_balance(db, "10")
    hold = billing.hold_user_balance(db, user.id, Decimal("0.10"))
    assert held_of(db, user.id) == Decimal("0.10") and hold.amount == Decimal("0.10")
    with pytest.raises(ValueError):
        billing.hold_user_balance(db, user.id, Decimal("0"))


def test_hold_insufficient_is_http_402(db):
    user, _ = user_with_balance(db, "0.02")
    with pytest.raises(HTTPException) as e:
        billing.hold_user_balance(db, user.id)
    assert isinstance(e.value, billing.InsufficientFunds)
    assert e.value.status_code == 402 and "0.03" in e.value.detail
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("0.02"), Decimal("0"))


def test_confirm_charges_actual_cost(db):
    user, _ = user_with_balance(db, "10")
    hold = billing.hold_user_balance(db, user.id)
    charged = billing.confirm_user_charge(db, hold, USAGE)
    assert charged == Decimal("0.01")
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("9.99"), Decimal("0"))
    assert db.get(Transaction, hold.transaction_id).amount == Decimal("0.01")
    assert db.scalar(select(AILog.charged)) == Decimal("0.01")
    assert_ledger_matches(db, user.id)


def test_refund_returns_everything(db):
    user, _ = user_with_balance(db, "10")
    hold = billing.hold_user_balance(db, user.id)
    billing.refund_user_balance(db, hold)
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("10"), Decimal("0"))
    assert_ledger_matches(db, user.id)


@pytest.mark.parametrize("first,second", [
    ("confirm", "confirm"), ("refund", "refund"), ("confirm", "refund"), ("refund", "confirm")])
def test_double_settlement_rejected_by_db(db, first, second):
    """Второй расчёт по тому же резерву отменяется базой (CHECK held_balance >= 0), а не проходит молча."""
    user, _ = user_with_balance(db, "10")
    hold = billing.hold_user_balance(db, user.id)
    ops = {"confirm": lambda: billing.confirm_user_charge(db, hold, USAGE),
           "refund": lambda: billing.refund_user_balance(db, hold)}
    ops[first]()
    expected_balance = balance_of(db, user.id)
    with pytest.raises(IntegrityError):
        ops[second]()
    db.rollback()
    assert (balance_of(db, user.id), held_of(db, user.id)) == (expected_balance, Decimal("0"))
    assert_ledger_matches(db, user.id)


def test_holds_cannot_exceed_balance(db):
    # Три заморозки по 0.03 при балансе 0.07: третья не проходит, баланс не уходит в минус
    user, _ = user_with_balance(db, "0.07")
    billing.hold_user_balance(db, user.id)
    billing.hold_user_balance(db, user.id)
    with pytest.raises(billing.InsufficientFunds):
        billing.hold_user_balance(db, user.id)
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("0.01"), Decimal("0.06"))
