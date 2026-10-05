"""Служебные команды. Запуск из папки backend/:

    python -m app.cli create-admin admin@mail.ru            # спросит пароль
    python -m app.cli make-admin user@mail.ru
    python -m app.cli add-balance user@mail.ru 1000
"""
import argparse
import getpass
import os
import sys
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import Transaction, TransactionType, User, UserRole


def create_admin(email: str, password: str) -> int:
    """Создаёт администратора (или назначает админом и меняет пароль существующему)."""
    from pydantic import ValidationError

    from app.auth import get_password_hash
    from app.schemas import UserCreate

    try:
        data = UserCreate(email=email, password=password)  # те же правила, что при регистрации
    except ValidationError as e:
        print("Ошибка:", "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors()))
        return 1
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == data.email))
        if user is None:
            db.add(User(email=data.email, hashed_password=get_password_hash(data.password),
                        role=UserRole.ADMIN))
            action = "создан"
        else:
            user.role = UserRole.ADMIN
            user.hashed_password = get_password_hash(data.password)
            action = "уже был — назначен админом, пароль обновлён"
        db.commit()
    print(f"Администратор {data.email} {action}")
    return 0


def make_admin(email: str) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            print(f"Пользователь {email} не найден. Сначала зарегистрируйте его через API.")
            return 1
        user.role = UserRole.ADMIN
        db.commit()
    print(f"{email} теперь администратор")
    return 0


def add_balance(email: str, amount: str) -> int:
    try:
        value = Decimal(amount)
    except InvalidOperation:
        print(f"Некорректная сумма: {amount}")
        return 1
    if value <= 0 or value != value.quantize(Decimal("0.01")):
        print("Сумма должна быть больше 0 и не больше 2 знаков после запятой")
        return 1
    with SessionLocal() as db:
        # Атомарно: параллельные списания за клики не потеряются
        user_id = db.scalar(
            update(User).where(User.email == email.lower())
            .values(balance=User.balance + value).returning(User.id)
        )
        if user_id is None:
            print(f"Пользователь {email} не найден")
            return 1
        db.add(Transaction(user_id=user_id, amount=value, type=TransactionType.DEPOSIT,
                           description="Пополнение администратором (CLI)"))
        db.commit()
        balance = db.get(User, user_id).balance
    print(f"Баланс {email} пополнен на {value}, теперь {balance}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    ca = sub.add_parser("create-admin", help="Создать администратора с паролем")
    ca.add_argument("email")
    ca.add_argument("--password", help="Пароль (иначе берётся из ADMIN_PASSWORD или спрашивается)")
    sub.add_parser("make-admin", help="Назначить пользователя администратором").add_argument("email")
    topup = sub.add_parser("add-balance", help="Пополнить баланс пользователя")
    topup.add_argument("email")
    topup.add_argument("amount")
    args = parser.parse_args()
    if args.command == "create-admin":
        password = args.password or os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Пароль: ")
        return create_admin(args.email, password)
    if args.command == "make-admin":
        return make_admin(args.email)
    if args.command == "add-balance":
        return add_balance(args.email, args.amount)
    return 1


if __name__ == "__main__":
    sys.exit(main())
