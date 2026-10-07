from decimal import Decimal

import pytest  # noqa: F401  фикстуры client и auth_headers берутся из tests/conftest.py
from fastapi.testclient import TestClient

from app.main import app
from app.models import CampaignStatus, TransactionType


def visitor(ip: str) -> TestClient:
    """Посетитель сайта со своим IP: повторные клики с одного IP не оплачиваются."""
    return TestClient(app, client=(ip, 50000))


def test_full_financial_and_ad_lifecycle(client, auth_headers):
    """
    Интеграционный тест полного цикла:
    Deposit -> Serve -> Click -> Ledger Transaction -> Нет денег (не показывается) -> Пополнение (снова показывается)
    """
    # 1. Создаем рекламную площадку (цена клика = 50.00)
    placement_resp = client.post(
        "/api/v1/placements/",
        json={"name": "Главный Баннер", "code_identifier": "lifecycle_banner", "price_per_click": 50.0},
        headers=auth_headers,
    )
    assert placement_resp.status_code == 201
    placement_id = placement_resp.json()["id"]

    # 2. Пополняем кошелек на 100.00 (хватит ровно на 2 клика)
    deposit_resp = client.post("/api/v1/wallet/deposit", json={"amount": 100.0}, headers=auth_headers)
    assert deposit_resp.status_code == 200
    assert Decimal(deposit_resp.json()["balance"]) == Decimal("100")

    # 3. Создаем кампанию (черновик)
    campaign_resp = client.post(
        "/api/v1/campaigns/",
        json={"title": "Тестовая кампания Биллинга", "target_url": "https://example.com",
              "placement_id": placement_id},
        headers=auth_headers,
    )
    assert campaign_resp.status_code == 201
    assert campaign_resp.json()["status"] == CampaignStatus.DRAFT
    campaign_id = campaign_resp.json()["id"]

    # Черновик не показывается
    assert client.get("/api/v1/ad/serve?placement_code=lifecycle_banner").status_code == 404

    # 4. Отправляем на модерацию и одобряем (ACTIVE)
    submit_resp = client.post(f"/api/v1/campaigns/{campaign_id}/submit", headers=auth_headers)
    assert submit_resp.json()["status"] == CampaignStatus.MODERATION
    mod_resp = client.patch(f"/api/v1/campaigns/{campaign_id}/moderate",
                            json={"status": "active"}, headers=auth_headers)
    assert mod_resp.status_code == 200
    assert mod_resp.json()["status"] == CampaignStatus.ACTIVE

    # 5. Выдача рекламы (GET /serve) — публичная, без токена
    serve_resp = client.get("/api/v1/ad/serve?placement_code=lifecycle_banner")
    assert serve_resp.status_code == 200
    ad = serve_resp.json()
    assert ad["campaign_id"] == campaign_id
    assert "target_url" not in ad  # переход только через click_url (для учёта кликов)
    assert f"/api/v1/ad/click/{campaign_id}?p=" in ad["click_url"]

    # 6. Клик #1: списание 50.00, остаток 50.00
    click1_resp = visitor("1.1.1.1").get(ad["click_url"], follow_redirects=False)
    assert click1_resp.status_code == 302
    assert click1_resp.headers["location"] == "https://example.com/"
    bal1_resp = client.get("/api/v1/wallet/balance", headers=auth_headers)
    assert Decimal(bal1_resp.json()["balance"]) == Decimal("50")

    # Повторный клик того же посетителя не оплачивается (защита от накрутки), но редирект есть
    assert visitor("1.1.1.1").get(ad["click_url"], follow_redirects=False).status_code == 302
    bal_dup_resp = client.get("/api/v1/wallet/balance", headers=auth_headers)
    assert Decimal(bal_dup_resp.json()["balance"]) == Decimal("50")

    # 7. Клик #2 от другого посетителя: списание 50.00, остаток 0.00
    assert visitor("2.2.2.2").get(ad["click_url"], follow_redirects=False).status_code == 302
    bal2_resp = client.get("/api/v1/wallet/balance", headers=auth_headers)
    assert Decimal(bal2_resp.json()["balance"]) == Decimal("0")

    camp_resp = client.get(f"/api/v1/campaigns/{campaign_id}", headers=auth_headers)
    assert camp_resp.json()["clicks_count"] == 2
    # Статус не меняется: кампания одобрена, просто владельцу нечем платить
    assert camp_resp.json()["status"] == CampaignStatus.ACTIVE

    # Без денег кампания не выдается на /serve
    assert client.get("/api/v1/ad/serve?placement_code=lifecycle_banner").status_code == 404

    # Клик по старому баннеру: посетитель всё равно попадает на сайт, но деньги не списываются
    click3_resp = visitor("3.3.3.3").get(ad["click_url"], follow_redirects=False)
    assert click3_resp.status_code == 302
    bal3_resp = client.get("/api/v1/wallet/balance", headers=auth_headers)
    assert Decimal(bal3_resp.json()["balance"]) == Decimal("0")

    # 8. История транзакций (Ledger): 1 deposit и 2 click_spend, новые сверху
    history_resp = client.get("/api/v1/wallet/history", headers=auth_headers)
    assert history_resp.status_code == 200
    history = history_resp.json()["items"]
    assert history_resp.json()["total"] == 3
    assert [t["type"] for t in history] == [
        TransactionType.CLICK_SPEND, TransactionType.CLICK_SPEND, TransactionType.DEPOSIT,
    ]
    assert all(t["campaign_id"] == campaign_id for t in history[:2])
    # Журнал сходится с балансом
    ledger = sum(-Decimal(t["amount"]) if t["type"] == TransactionType.CLICK_SPEND else Decimal(t["amount"])
                 for t in history)
    assert ledger == Decimal("0")

    # 9. Пополняем снова на 100.00 — кампания сразу возвращается в выдачу
    deposit2_resp = client.post("/api/v1/wallet/deposit", json={"amount": 100.0}, headers=auth_headers)
    assert Decimal(deposit2_resp.json()["balance"]) == Decimal("100")

    serve_again_resp = client.get("/api/v1/ad/serve?placement_code=lifecycle_banner")
    assert serve_again_resp.status_code == 200
    assert serve_again_resp.json()["campaign_id"] == campaign_id
    camp_resp = client.get(f"/api/v1/campaigns/{campaign_id}", headers=auth_headers)
    assert camp_resp.json()["status"] == CampaignStatus.ACTIVE


def test_get_campaign_access(client, auth_headers, db):
    """GET /campaigns/{id}: владелец и админ видят, чужой — 404."""
    from app.auth import create_access_token
    from app.models import Campaign, Placement, User

    owner = User(email="owner@example.com", hashed_password="x")
    other = User(email="other@example.com", hashed_password="x")
    placement = Placement(name="P", code_identifier="p")
    db.add_all([owner, other, placement])
    db.commit()
    c = Campaign(user_id=owner.id, placement_id=placement.id, title="T", target_url="https://a.ru/")
    db.add(c)
    db.commit()

    def get(user):
        return client.get(f"/api/v1/campaigns/{c.id}",
                          headers={"Authorization": f"Bearer {create_access_token(user.id)}"})

    assert get(owner).status_code == 200
    assert get(other).status_code == 404
    assert client.get(f"/api/v1/campaigns/{c.id}", headers=auth_headers).status_code == 200
    assert client.get("/api/v1/campaigns/999", headers=auth_headers).status_code == 404
    assert client.get(f"/api/v1/campaigns/{c.id}").status_code == 401
