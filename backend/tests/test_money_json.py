"""Деньги: внутри Decimal, в JSON — число с 2 знаками; CTR — всегда число."""
import json
from decimal import Decimal

import pytest

from app.auth import create_access_token
from app.models import User
from app.schemas import (
    AnalyticsSummaryResponse, AnalyticsTotals, CampaignAnalyticsItem, CampaignTotals, DailyAnalytics,
    DayStat, MyStats, Totals, WalletBalanceResponse,
)


@pytest.mark.parametrize("value,expected", [
    ("0.10", 0.1), ("0.30", 0.3), ("95", 95.0), ("2.5", 2.5), ("0", 0.0),
    ("9999999999.99", 9999999999.99),   # максимум Numeric(12, 2) — без искажений
    ("12.345", 12.35),                  # лишние знаки — до копеек, арифметически (половина вверх)
])
def test_money_serialized_as_number(value, expected):
    dumped = WalletBalanceResponse(balance=Decimal(value)).model_dump_json()
    assert json.loads(dumped)["balance"] == expected
    assert dumped == f'{{"balance":{expected!r}}}'


def test_money_stays_decimal_inside():
    m = WalletBalanceResponse(balance=Decimal("0.10"))
    assert isinstance(m.balance, Decimal)
    assert isinstance(m.model_dump()["balance"], Decimal)  # Python-режим — Decimal для расчётов
    total = sum([Decimal("0.10"), Decimal("0.20")])
    assert WalletBalanceResponse(balance=total).model_dump(mode="json")["balance"] == 0.3


def test_ctr_always_number():
    t = Totals(impressions=0, clicks=0, spend=Decimal("0"), ctr=0.0)
    assert json.loads(t.model_dump_json()) == {"impressions": 0, "clicks": 0, "spend": 0.0, "ctr": 0.0}


def test_tutorial_aliases():
    assert AnalyticsSummaryResponse is MyStats and AnalyticsTotals is Totals
    assert DailyAnalytics is DayStat and CampaignAnalyticsItem is CampaignTotals


def test_analytics_summary_route_equals_stats_me(client, db):
    u = User(email="a@mail.ru", hashed_password="x", balance=Decimal("12.50"))
    db.add(u)
    db.commit()
    h = {"Authorization": f"Bearer {create_access_token(u.id)}"}
    a = client.get("/api/v1/analytics/summary", params={"days": 7}, headers=h)
    b = client.get("/api/v1/stats/me", params={"days": 7}, headers=h)
    assert a.status_code == 200 and a.json() == b.json()
    assert a.json()["balance"] == 12.5 and isinstance(a.json()["totals"]["ctr"], float)
    assert client.get("/api/v1/analytics/summary").status_code == 401


def test_money_input_accepts_string_and_number(client, auth_headers):
    for amount, expected in [("10.25", 10.25), (5, 15.25), (0.75, 16.0)]:
        r = client.post("/api/v1/wallet/deposit", json={"amount": amount}, headers=auth_headers)
        assert r.json() == {"balance": expected}


def test_openapi_documents_money_as_number(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert schema["WalletBalanceResponse"]["properties"]["balance"]["type"] == "number"
