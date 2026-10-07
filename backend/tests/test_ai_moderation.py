"""AI-проверка в процессе модерации: фон после отправки, кнопка админа, видимость, гонки.

Модель не вызывается: ai.moderate_ad подменяется.
"""
import pytest

from app import ai, moderation
from app.config import settings
from app.models import Campaign, CampaignStatus

from tests.test_api_campaigns import URL, admin_headers, create, headers_for, placement  # noqa: F401

REJECT = ai.AIModerationResult(verdict="reject", risk="high", reasons=["Финансовая пирамида", "Фишинг"],
                               summary="Мошенничество")
APPROVE = ai.AIModerationResult(verdict="approve", risk="low", reasons=[], summary="Обычное объявление")


@pytest.fixture
def fake_ai(monkeypatch):
    """Включает AI и подменяет модель. state.result — ответ (или исключение), state.calls — вызовы."""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    # Ручной режим: модель только подсказывает (автопилот — отдельные тесты в конце файла)
    monkeypatch.setattr(settings, "ai_auto_reject", False)
    monkeypatch.setattr(settings, "ai_auto_approve", False)

    class State:
        result = APPROVE
        calls = []
        before_return = None  # колбэк «пока модель думает»

    def moderate_ad(title, description, target_url, image_url):
        State.calls.append(title)
        if State.before_return:
            State.before_return()
        if isinstance(State.result, Exception):
            raise State.result
        return State.result

    monkeypatch.setattr(ai, "moderate_ad", moderate_ad)
    return State


def submit(client, h, cid):
    r = client.post(f"{URL}/{cid}/submit", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def fresh(db, cid) -> Campaign:
    db.expire_all()
    return db.get(Campaign, cid)


def test_submit_runs_ai_in_background(client, db, placement, fake_ai):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    fake_ai.result = REJECT
    body = submit(client, h, cid)
    # Рекламодателю — только статус, без подсказки AI
    assert body["status"] == "moderation" and "ai_verdict" not in body
    c = fresh(db, cid)
    assert (c.ai_verdict, c.ai_risk, c.ai_reasons, c.ai_summary) == ("reject", "high", REJECT.reasons, "Мошенничество")
    assert c.ai_checked_at is not None
    assert c.status == CampaignStatus.MODERATION  # без AI_AUTO_REJECT решает только человек


def test_admin_sees_ai_hint_advertiser_does_not(client, db, placement, fake_ai):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    fake_ai.result = REJECT
    submit(client, h, cid)

    [item] = client.get(URL, params={"status": "moderation"}, headers=admin_headers(db)).json()["items"]
    assert item["ai_verdict"] == "reject" and item["ai_reasons"] == REJECT.reasons
    assert item["ai_checked_at"].endswith("Z") or "+00:00" in item["ai_checked_at"]

    [own] = client.get(URL, headers=h).json()["items"]
    assert own["id"] == cid
    assert all(own[f] is None for f in ("ai_verdict", "ai_risk", "ai_reasons", "ai_summary", "ai_checked_at"))
    assert "ai_verdict" not in client.get(f"{URL}/{cid}", headers=h).json()


def test_disabled_ai_is_not_called(client, db, placement, fake_ai, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    submit(client, h, cid)
    assert fake_ai.calls == []
    assert fresh(db, cid).ai_verdict is None


def test_ai_error_keeps_campaign_in_queue(client, db, placement, fake_ai):
    fake_ai.result = ai.AIUnavailable("нет связи с Anthropic API")
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    submit(client, h, cid)
    c = fresh(db, cid)
    assert c.status == CampaignStatus.MODERATION
    assert (c.ai_verdict, c.ai_summary) == ("error", "нет связи с Anthropic API")


def test_auto_reject_only_high_risk(client, db, placement, fake_ai, monkeypatch):
    monkeypatch.setattr(settings, "ai_auto_reject", True)
    _, h = headers_for(db, "a@mail.ru")

    fake_ai.result = REJECT
    cid = create(client, h, placement)
    submit(client, h, cid)
    c = fresh(db, cid)
    assert c.status == CampaignStatus.REJECTED
    assert c.rejection_reason == moderation.AUTO_REJECT_PREFIX + "Финансовая пирамида; Фишинг"
    # Рекламодатель видит причину и может исправить и отправить заново
    assert client.get(f"{URL}/{cid}", headers=h).json()["rejection_reason"].startswith("Автоматическая")

    # reject со средним риском — только подсказка, решает человек
    fake_ai.result = ai.AIModerationResult(verdict="reject", risk="medium", reasons=["Сомнительно"], summary="")
    cid2 = create(client, h, placement)
    submit(client, h, cid2)
    assert fresh(db, cid2).status == CampaignStatus.MODERATION


def test_late_result_does_not_override_admin_decision(client, db, placement, fake_ai):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)
    fake_ai.result = REJECT

    def admin_approves_meanwhile():
        r = client.patch(f"{URL}/{cid}/moderate", json={"status": "active"}, headers=ha)
        assert r.status_code == 200

    fake_ai.before_return = admin_approves_meanwhile
    submit(client, h, cid)
    c = fresh(db, cid)
    assert c.status == CampaignStatus.ACTIVE and c.ai_verdict is None


def test_late_result_for_changed_content_is_dropped(client, db, placement, fake_ai):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    submit(client, h, cid)

    def content_changed_meanwhile():
        c = fresh(db, cid)
        c.title = "Другой заголовок"
        db.commit()

    fake_ai.before_return = content_changed_meanwhile
    assert moderation.run_ai_review(lambda: type(db)(bind=db.get_bind()), cid) is None


def test_resubmit_resets_previous_ai_result(client, db, placement, fake_ai):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)
    fake_ai.result = REJECT
    submit(client, h, cid)
    client.patch(f"{URL}/{cid}/moderate", json={"status": "rejected", "rejection_reason": "Нет"}, headers=ha)

    fake_ai.result = ai.AIUnavailable("сбой")
    submit(client, h, cid)
    c = fresh(db, cid)
    # Старый «reject» не должен остаться у новой версии объявления
    assert (c.ai_verdict, c.ai_reasons, c.ai_risk) == ("error", None, None)


def test_admin_rerun_endpoint(client, db, placement, fake_ai, monkeypatch):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)

    # Не на модерации — 409
    assert client.post(f"{URL}/{cid}/ai-review", headers=ha).status_code == 409
    fake_ai.result = ai.AIUnavailable("сбой")
    submit(client, h, cid)

    fake_ai.result = APPROVE
    r = client.post(f"{URL}/{cid}/ai-review", headers=ha)
    assert r.status_code == 200
    assert (r.json()["ai_verdict"], r.json()["ai_risk"], r.json()["owner_email"]) == ("approve", "low", "a@mail.ru")

    assert client.post(f"{URL}/{cid}/ai-review", headers=h).status_code == 403
    assert client.post(f"{URL}/99999/ai-review", headers=ha).status_code == 404
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert client.post(f"{URL}/{cid}/ai-review", headers=ha).status_code == 503


def test_ai_status_admin_only(client, db, fake_ai):
    _, h = headers_for(db, "a@mail.ru")
    r = client.get(f"{URL}/ai-status", headers=admin_headers(db))
    assert r.json() == {"enabled": True, "model": settings.ai_model, "auto_reject": False, "auto_approve": False}
    assert client.get(f"{URL}/ai-status", headers=h).status_code == 403


# ---------- Автопилот: модель уверена — решение без модератора ----------
@pytest.fixture
def autopilot(monkeypatch, fake_ai):
    monkeypatch.setattr(settings, "ai_auto_approve", True)
    monkeypatch.setattr(settings, "ai_auto_reject", True)
    return fake_ai


def test_autopilot_approves_confident_clean_ad(client, db, placement, autopilot):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    submit(client, h, cid)
    assert fresh(db, cid).status == CampaignStatus.ACTIVE


@pytest.mark.parametrize("result", [
    ai.AIModerationResult(verdict="approve", risk="medium", reasons=[], summary=""),
    ai.AIModerationResult(verdict="review", risk="low", reasons=["Кликбейт"], summary=""),
    ai.AIModerationResult(verdict="reject", risk="medium", reasons=["Сомнительно"], summary=""),
])
def test_autopilot_leaves_doubts_to_human(client, db, placement, autopilot, result):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    autopilot.result = result
    submit(client, h, cid)
    assert fresh(db, cid).status == CampaignStatus.MODERATION


def test_autopilot_does_not_approve_stop_words(client, db, placement, autopilot):
    _, h = headers_for(db, "a@mail.ru")
    r = client.post(URL, json={"placement_id": placement.id, "title": "Лучшее онлайн-казино",
                               "target_url": "https://example.com"}, headers=h)
    cid = r.json()["id"]
    submit(client, h, cid)  # модель (подменённая) «одобрила», но стоп-фраза — значит, решает человек
    assert fresh(db, cid).status == CampaignStatus.MODERATION


def test_autopilot_model_error_goes_to_human(client, db, placement, autopilot):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    autopilot.result = ai.AIUnavailable("timeout")
    submit(client, h, cid)
    assert fresh(db, cid).status == CampaignStatus.MODERATION


def test_autopilot_rejects_confident_violation(client, db, placement, autopilot):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    autopilot.result = REJECT
    submit(client, h, cid)
    c = fresh(db, cid)
    assert c.status == CampaignStatus.REJECTED and c.rejection_reason.startswith(moderation.AUTO_REJECT_PREFIX)


# ---------- Повтор, если модель не ответила ----------
def _stale(db, cid, *, verdict="error", checked_minutes_ago=10, updated_hours_ago=0):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    now = datetime.now(timezone.utc)
    db.execute(update(Campaign).where(Campaign.id == cid).values(
        ai_verdict=verdict, ai_checked_at=now - timedelta(minutes=checked_minutes_ago),
        updated_at=now - timedelta(hours=updated_hours_ago)))
    db.commit()


def _submitted(client, db, placement, fake_ai, title="Курсы"):
    _, h = headers_for(db, f"{title}@mail.ru")
    r = client.post(URL, json={"placement_id": placement.id, "title": title, "target_url": "https://example.com"},
                    headers=h)
    cid = r.json()["id"]
    fake_ai.result = ai.AIUnavailable("перегрузка")
    submit(client, h, cid)
    return cid


def test_retry_after_model_error(client, db, placement, fake_ai):
    from app.database import background_session_factory
    cid = _submitted(client, db, placement, fake_ai)
    assert fresh(db, cid).ai_verdict == "error"
    _stale(db, cid)
    fake_ai.result = APPROVE
    assert moderation.retry_failed_reviews(background_session_factory(db)) == 1
    assert fresh(db, cid).ai_verdict == "approve"


@pytest.mark.parametrize("kw", [dict(checked_minutes_ago=1),          # только что пробовали — подождать
                                dict(updated_hours_ago=25),           # прошли сутки — решает человек
                                dict(verdict="review")])              # модель ответила — не повторяем
def test_no_retry(client, db, placement, fake_ai, kw):
    from app.database import background_session_factory
    cid = _submitted(client, db, placement, fake_ai)
    _stale(db, cid, **kw)
    assert moderation.retry_failed_reviews(background_session_factory(db)) == 0


def test_retry_never_checked(client, db, placement, fake_ai):
    from app.database import background_session_factory
    cid = _submitted(client, db, placement, fake_ai)
    _stale(db, cid, verdict=None)
    fake_ai.result = APPROVE
    assert moderation.retry_failed_reviews(background_session_factory(db)) == 1


def test_no_retry_without_ai(db, monkeypatch):
    from app.database import background_session_factory
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert moderation.retry_failed_reviews(background_session_factory(db)) == 0


def test_periodic_loop_calls_retry(db, monkeypatch):
    from app.database import background_session_factory
    from app.services import ai_cleanup, site_check
    calls = []
    monkeypatch.setattr(moderation, "retry_failed_reviews", lambda f: calls.append("campaigns") or 1)
    monkeypatch.setattr(site_check, "retry_errors", lambda f: calls.append("sites") or 0)
    ai_cleanup.retry_ai_reviews_sync(background_session_factory(db))
    assert calls == ["campaigns", "sites"]
