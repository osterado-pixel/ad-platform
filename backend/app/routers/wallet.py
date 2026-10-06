from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db, write_lock
from app.ledger import add_transaction
from app.models import Transaction, TransactionType, User
from app.pagination import before_id_param, limit_param, offset_param
from app.schemas import DepositRequest, PaginatedResponse, TransactionResponse, WalletBalanceResponse

router = APIRouter(prefix="/api/v1/wallet", tags=["Кошелек и Баланс (Wallet)"])


# 1. Пополнение баланса
@router.post("/deposit", response_model=WalletBalanceResponse)
def deposit_funds(
    deposit_data: DepositRequest,
    user_id: int | None = Query(default=None, description="Кому пополнить; по умолчанию — себе"),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """
    Зачисляет деньги на баланс. Пока нет платёжной системы, пополнять может только
    администратор — иначе любой пользователь начислил бы себе деньги без оплаты.
    """
    target_id = user_id if user_id is not None else admin_user.id

    # Та же очередь записей SQLite, что и у кликов: без ожидания с паузами
    with write_lock():
        # Атомарно на стороне БД: параллельное списание за клик не потеряется
        row = db.execute(
            update(User)
            .where(User.id == target_id)
            .values(balance=User.balance + deposit_data.amount)
            .returning(User.balance, User.held_balance)
        ).first()
        if row is None:
            db.rollback()  # UPDATE уже открыл транзакцию записи — освобождаем сразу
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

        # Фиксируем транзакцию в истории — в той же транзакции БД, что и зачисление
        add_transaction(
            db, user_id=target_id, amount=deposit_data.amount, type=TransactionType.DEPOSIT,
            description=f"Пополнение баланса на {deposit_data.amount:.2f}",
        )
        db.commit()

    # Кампании возобновлять не нужно: /serve показывает их снова, как только баланса хватает на клик
    return WalletBalanceResponse(balance=row.balance, held_balance=row.held_balance)


# 2. Получение текущего баланса
@router.get("/balance", response_model=WalletBalanceResponse)
def get_balance(current_user: User = Depends(get_current_user)):
    return WalletBalanceResponse(balance=current_user.balance, held_balance=current_user.held_balance)


# 3. История транзакций
@router.get("/history", response_model=PaginatedResponse[TransactionResponse])
def get_transaction_history(
    response: Response,
    limit: int = limit_param(default=20),
    offset: int = offset_param(),
    before_id: int | None = before_id_param(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    История операций, новые сверху. У активного рекламодателя — по записи на каждый клик,
    поэтому всё рассчитано на миллионы строк:
    - сортировка по id (индекс (user_id, id)), а не по created_at: страница за ~1 мс
      вместо ~230 мс при 900 тыс. записей, и порядок однозначен (время — с точностью до секунды);
    - total — из счётчика users.transactions_count, без COUNT(*) по всей истории;
    - для прокрутки дальше 10 000 записей — курсор before_id (заголовок X-Next-Before-Id).
    """
    query = select(Transaction).where(Transaction.user_id == current_user.id)
    if before_id is not None:
        query = query.where(Transaction.id < before_id)
    rows = db.scalars(query.order_by(Transaction.id.desc()).limit(limit + 1).offset(offset)).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    response.headers["X-Has-More"] = "true" if has_more else "false"
    if has_more:
        response.headers["X-Next-Before-Id"] = str(items[-1].id)
    return {"items": items, "total": current_user.transactions_count, "limit": limit, "offset": offset}
