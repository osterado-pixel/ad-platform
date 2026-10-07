from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import (
    Campaign, CampaignStatus, Placement, Transaction, TransactionType, User, UserRole,
)
from app.schemas import (
    CampaignCreate, CampaignModerate, CampaignResponse, DepositRequest, PlacementCreate,
    PlacementResponse, Token, TransactionResponse, UserCreate, UserResponse, WalletBalanceResponse,
)


def test_email_lowercased():
    assert UserCreate(email="Test@Mail.RU", password="12345678").email == "test@mail.ru"


@pytest.mark.parametrize("email", ["bad", "a@", "@b.ru"])
def test_invalid_email_rejected(email):
    with pytest.raises(ValidationError):
        UserCreate(email=email, password="12345678")


@pytest.mark.parametrize("password", ["", "1234567", "x" * 73])
def test_password_length(password):
    with pytest.raises(ValidationError):
        UserCreate(email="a@b.ru", password=password)


def test_user_response_from_orm():
    u = User(id=1, email="a@b.ru", hashed_password="h", role=UserRole.ADMIN,
             balance=Decimal("10.10"), held_balance=Decimal("0.03"), created_at=datetime.now(timezone.utc))
    data = UserResponse.model_validate(u)
    assert data.role is UserRole.ADMIN
    assert data.balance == Decimal("10.10")
    dumped = data.model_dump(mode="json")
    assert dumped["role"] == "admin"
    assert dumped["balance"] == 10.1 and dumped["held_balance"] == 0.03
    assert "hashed_password" not in dumped


def test_token_default_type():
    assert Token(access_token="abc").token_type == "bearer"


def test_placement_create_valid():
    p = PlacementCreate(name="Главный баннер", code_identifier="main_banner",
                        price_per_day=100, price_per_click="2.50")
    assert p.price_per_day == Decimal("100")
    assert p.price_per_click == Decimal("2.50")
    assert p.is_active is True


@pytest.mark.parametrize("override", [
    {"name": ""},
    {"code_identifier": "bad id!"},
    {"code_identifier": "x" * 101},
    {"price_per_day": -1},
    {"price_per_click": "0.005"},
])
def test_placement_create_invalid(override):
    data = {"name": "Баннер", "code_identifier": "banner", **override}
    with pytest.raises(ValidationError):
        PlacementCreate(**data)


def test_placement_response_from_orm():
    p = Placement(id=5, name="Баннер", code_identifier="banner",
                  price_per_day=Decimal("100.00"), price_per_click=Decimal("1.50"), is_active=True)
    dumped = PlacementResponse.model_validate(p).model_dump(mode="json")
    assert dumped == {"id": 5, "name": "Баннер", "code_identifier": "banner",
                      "price_per_day": 100.0, "price_per_click": 1.5, "is_active": True, "site_id": None}


CAMPAIGN = {"placement_id": 1, "title": "Осенняя распродажа", "target_url": "https://shop.ru/sale?x=1"}


def test_campaign_create_valid():
    c = CampaignCreate(**CAMPAIGN, image_url="http://cdn.ru/a.png")
    assert c.target_url == "https://shop.ru/sale?x=1"
    assert isinstance(c.target_url, str)
    assert c.image_url == "http://cdn.ru/a.png"
    assert c.description is None


@pytest.mark.parametrize("override", [
    {"title": ""},
    {"title": "x" * 256},
    {"placement_id": 0},
    {"target_url": "javascript:alert(1)"},
    {"target_url": "ftp://shop.ru"},
    {"target_url": "not a url"},
    {"target_url": "https://shop.ru/" + "a" * 2048},
    {"image_url": "data:image/png;base64,AAAA"},
])
def test_campaign_create_invalid(override):
    with pytest.raises(ValidationError):
        CampaignCreate(**{**CAMPAIGN, **override})


def test_campaign_response_from_orm():
    c = Campaign(id=7, user_id=2, placement_id=1, title="T", target_url="https://shop.ru/",
                 status=CampaignStatus.MODERATION, clicks_count=0, impressions_count=0,
                 created_at=datetime.now(timezone.utc))
    dumped = CampaignResponse.model_validate(c).model_dump(mode="json")
    assert dumped["status"] == "moderation"
    assert dumped["user_id"] == 2
    assert dumped["rejection_reason"] is None


def test_naive_db_datetime_marked_utc():
    c = Campaign(id=1, user_id=1, placement_id=1, title="T", target_url="https://a.ru/",
                 status=CampaignStatus.DRAFT, clicks_count=0, impressions_count=0,
                 created_at=datetime(2026, 10, 5, 13, 59, 43),
                 start_date=datetime(2026, 10, 6))
    dumped = CampaignResponse.model_validate(c).model_dump(mode="json")
    assert dumped["created_at"] == "2026-10-05T13:59:43Z"
    assert dumped["start_date"] == "2026-10-06T00:00:00Z"


@pytest.mark.parametrize("schema", [UserCreate, PlacementCreate, CampaignCreate])
def test_docs_examples_are_valid(schema):
    # Пример из /docs ("Try it out") должен проходить собственную валидацию
    for example in schema.model_json_schema()["examples"]:
        schema.model_validate(example)


def test_moderate_approve():
    m = CampaignModerate(status="active", rejection_reason="лишнее")
    assert m.status is CampaignStatus.ACTIVE
    assert m.rejection_reason is None


def test_moderate_reject_with_reason():
    m = CampaignModerate(status="rejected", rejection_reason="  Нарушение правил  ")
    assert m.status is CampaignStatus.REJECTED
    assert m.rejection_reason == "Нарушение правил"


@pytest.mark.parametrize("data", [
    {"status": "rejected"},
    {"status": "rejected", "rejection_reason": "   "},
    {"status": "draft"},
    {"status": "completed"},
    {"status": "hacked"},
    {"status": "rejected", "rejection_reason": "x" * 1001},
])
def test_moderate_invalid(data):
    with pytest.raises(ValidationError):
        CampaignModerate(**data)


def test_moderate_docs_examples_valid():
    for example in CampaignModerate.model_json_schema()["examples"]:
        CampaignModerate.model_validate(example)


@pytest.mark.parametrize("amount,expected", [(1000, "1000"), ("0.01", "0.01"), (99.5, "99.5")])
def test_deposit_valid(amount, expected):
    assert DepositRequest(amount=amount).amount == Decimal(expected)


@pytest.mark.parametrize("amount", [0, -1, "0.001", 1_000_001, "abc", None])
def test_deposit_invalid(amount):
    with pytest.raises(ValidationError):
        DepositRequest(amount=amount)


def test_transaction_response_from_orm():
    t = Transaction(id=1, user_id=2, amount=Decimal("5.00"), type=TransactionType.CLICK_SPEND,
                    campaign_id=3, description="Клик", created_at=datetime(2026, 10, 5, 12, 0))
    assert TransactionResponse.model_validate(t).model_dump(mode="json") == {
        "id": 1, "user_id": 2, "amount": 5.0, "type": "click_spend", "campaign_id": 3,
        "description": "Клик", "created_at": "2026-10-05T12:00:00Z",
    }


def test_wallet_balance_response():
    assert WalletBalanceResponse(balance=Decimal("95.00")).model_dump(mode="json") == {"balance": 95.0, "held_balance": 0.0}


def test_paginated_response_generic():
    from app.schemas import PaginatedResponse
    page = PaginatedResponse[TransactionResponse].model_validate({
        "items": [{"id": 1, "user_id": 2, "amount": "5.00", "type": "deposit",
                   "created_at": "2026-10-05T12:00:00Z"}],
        "total": 41, "limit": 20, "offset": 20,
    })
    assert isinstance(page.items[0], TransactionResponse)
    assert page.model_dump(mode="json")["items"][0]["amount"] == 5.0  # деньги — числом, как везде
    schema = PaginatedResponse[TransactionResponse].model_json_schema()
    assert schema["properties"]["items"]["items"]["$ref"].endswith("TransactionResponse")
    for bad in [{"total": -1}, {"limit": 0}, {"offset": -5}]:
        with pytest.raises(ValidationError):
            PaginatedResponse[TransactionResponse].model_validate({"items": [], "total": 0, "limit": 20, "offset": 0, **bad})
