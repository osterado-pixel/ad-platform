"""Тарифы: публичный список, «мой тариф», покупка (через платёж), управление для администратора.

Что даёт тариф, решает app/services/entitlements.py — сейчас тарифы только хранятся и покупаются.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db, write_lock
from app.models import PaymentPurpose, Plan, User
from app.payments import PaymentProviderError
from app.routers.payments import site_url
from app.schemas import MyPlan, PlanCreate, PlanPurchaseResponse, PlanResponse, PlanUpdate
from app.services import entitlements, payments_service

router = APIRouter(prefix="/api/v1/plans", tags=["Тарифы"])


@router.get("", response_model=list[PlanResponse])
def list_plans(db: Session = Depends(get_db)):
    """Действующие тарифы (для страницы цен). Без входа."""
    return db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.id)).all()


@router.get("/my", response_model=MyPlan)
def my_plan(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sub = entitlements.active_subscription(db, current_user.id)
    return MyPlan(subscription=sub, plan=db.get(Plan, sub.plan_id) if sub else None,
                  entitlements=entitlements.entitlements(db, current_user.id))


@router.post("/{code}/buy", response_model=PlanPurchaseResponse)
def buy_plan(code: str, request: Request, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    plan = db.scalar(select(Plan).where(Plan.code == code, Plan.is_active.is_(True)))
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    if plan.price == 0:  # бесплатный — сразу, без платежа
        with write_lock():
            sub = payments_service.activate_subscription(db, current_user.id, plan, datetime.now(timezone.utc))
            db.commit()
        db.refresh(sub)
        return PlanPurchaseResponse(subscription=sub)
    try:
        payment = payments_service.create_payment(db, current_user.id, PaymentPurpose.PLAN, plan.price,
                                                  return_url=f"{site_url(request)}/app#/wallet", plan=plan)
    except payments_service.PaymentsDisabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Приём платежей не подключён — тариф пока недоступен") from None
    except PaymentProviderError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Платёжная система не ответила — попробуйте позже") from None
    return PlanPurchaseResponse(payment=payment)


# --- Администратор ---
@router.post("", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
def create_plan(body: PlanCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    plan = Plan(**body.model_dump())
    with write_lock():
        db.add(plan)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Тариф с таким кодом уже есть") from None
    db.refresh(plan)
    return plan


@router.patch("/{plan_id}", response_model=PlanResponse)
def update_plan(plan_id: int, body: PlanUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Правка тарифа. Уже купленные подписки не меняются: их возможности сохранены при покупке.
    Отключить продажу — is_active=false (удалять тарифы нельзя: на них ссылаются платежи)."""
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    with write_lock():
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(plan, key, value)
        db.commit()
    db.refresh(plan)
    return plan
