"""Служебные команды. Запуск из папки backend/:

    python -m app.cli create-admin admin@mail.ru            # новому — спросит пароль дважды
    python -m app.cli make-admin user@mail.ru
    python -m app.cli add-balance user@mail.ru 1000
    python -m app.cli set-password user@mail.ru         # сброс забытого пароля (спросит новый)
    python -m app.cli backup-db backups/                # копия SQLite на ходу
    python -m app.cli purge --clicks-days 30            # очистка служебных записей
"""
import argparse
import getpass
import os
import sys
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select, update

from app.database import SessionLocal
from app.ledger import add_transaction
from app.models import AuthAttempt, Click, TransactionType, User, UserRole


def ask_password(prompt: str = "Пароль: ") -> str | None:
    """Спрашивает пароль дважды (ввод не отображается). None — если не совпали."""
    password = getpass.getpass(prompt)
    if password != getpass.getpass("Повторите пароль: "):
        print("❌ Пароли не совпадают!")
        return None
    return password


def _validate(email: str, password: str):
    """Те же правила, что при регистрации: email и длина пароля. None — ошибка уже напечатана."""
    from pydantic import ValidationError

    from app.schemas import UserCreate

    try:
        return UserCreate(email=email, password=password)
    except ValidationError as e:
        print("❌ Ошибка:", "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors()))
        return None


def create_admin(email: str, password: str | None = None, ask=ask_password) -> int:
    """Создаёт нового администратора или повышает существующего пользователя.

    Существующему пользователю пароль не меняется (для этого — set-password).
    Новому — пароль из аргумента/ADMIN_PASSWORD, иначе спрашивается дважды.
    """
    from app.auth import get_password_hash

    lookup = _validate(email, "x" * 8)  # проверить и нормализовать email (регистр)
    if lookup is None:
        return 1
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == lookup.email))
        if user is not None:
            if user.is_admin:
                print(f"ℹ Пользователь '{user.email}' уже является администратором!")
                return 0
            user.is_admin = True
            db.commit()
            print(f"✅ Пользователь '{user.email}' успешно повышен до администратора!")
            if password:
                print("ℹ Пароль существующего пользователя не изменён — для смены: set-password")
            return 0

        if not password:
            password = ask("Введите пароль для нового администратора: ")
            if password is None:
                return 1
        data = _validate(email, password)
        if data is None:
            return 1
        db.add(User(email=data.email, hashed_password=get_password_hash(data.password), is_admin=True))
        db.commit()
    print(f"✅ Новый администратор '{data.email}' успешно создан!")
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
        add_transaction(db, user_id=user_id, amount=value, type=TransactionType.DEPOSIT,
                        description="Пополнение администратором (CLI)")
        db.commit()
        balance = db.get(User, user_id).balance
    print(f"Баланс {email} пополнен на {value}, теперь {balance}")
    return 0


def set_password(email: str, password: str) -> int:
    """Сброс пароля (почты нет — сбрасывает администратор). Все токены пользователя отзываются."""
    from pydantic import ValidationError

    from app.auth import get_password_hash
    from app.schemas import UserCreate

    try:
        data = UserCreate(email=email, password=password)
    except ValidationError as e:
        print("Ошибка:", "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors()))
        return 1
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == data.email))
        if user is None:
            print(f"Пользователь {email} не найден")
            return 1
        user.hashed_password = get_password_hash(data.password)
        user.token_version += 1
        db.commit()
    print(f"Пароль {data.email} изменён, все сеансы пользователя завершены")
    return 0


def backup_db(target_dir: str) -> int:
    """Копия SQLite без остановки сервера (штатный backup API: согласованно и в режиме WAL)."""
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from app.config import settings
    from app.database import engine, is_sqlite

    if not is_sqlite:
        print("Для PostgreSQL используйте pg_dump (см. README, раздел «Резервные копии»)")
        return 1
    source = engine.url.database
    if not source or source == ":memory:":
        print("База в памяти — копировать нечего")
        return 1
    out_dir = Path(target_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"app-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print(f"Копия базы: {target} ({target.stat().st_size // 1024} КБ)")
    return 0


def purge(clicks_days: int) -> int:
    """Удаляет служебные записи: клики старше N дней (нужны только для защиты от повторов —
    статистика хранится по дням, деньги в журнале транзакций) и старые попытки входа."""
    import time

    from app.config import settings

    min_days = max(1, -(-settings.click_dedup_minutes // (24 * 60)))
    if clicks_days < min_days:
        print(f"Нельзя меньше {min_days} дн.: записи нужны для защиты от повторных кликов")
        return 1
    now = int(time.time())
    with SessionLocal() as db:
        clicks = db.execute(delete(Click).where(Click.time_window < now // 60 - clicks_days * 24 * 60)).rowcount
        attempts = db.execute(delete(AuthAttempt).where(AuthAttempt.ts < now - 24 * 3600)).rowcount
        db.commit()
    print(f"Удалено: кликов {clicks}, попыток входа {attempts}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="CLI управления рекламной платформой")
    sub = parser.add_subparsers(dest="command", help="Команды")
    ca = sub.add_parser("create-admin", help="Создать администратора или повысить существующего пользователя")
    ca.add_argument("email", help="Email администратора")
    ca.add_argument("--password", "-p", default=None,
                    help="Пароль нового администратора (иначе ADMIN_PASSWORD или спросит дважды)")
    sub.add_parser("make-admin", help="Назначить пользователя администратором").add_argument("email")
    sp = sub.add_parser("set-password", help="Сбросить пароль пользователю (сеансы завершаются)")
    sp.add_argument("email")
    sp.add_argument("--password", "-p", default=None, help="Новый пароль (иначе спросит дважды)")
    bk = sub.add_parser("backup-db", help="Резервная копия SQLite (без остановки сервера)")
    bk.add_argument("target_dir", nargs="?", default="backups")
    pg = sub.add_parser("purge", help="Удалить старые служебные записи (клики, попытки входа)")
    pg.add_argument("--clicks-days", type=int, default=30, help="Хранить клики N дней (по умолчанию 30)")
    topup = sub.add_parser("add-balance", help="Пополнить баланс пользователя")
    topup.add_argument("email")
    topup.add_argument("amount")
    args = parser.parse_args()
    if args.command == "create-admin":
        return create_admin(args.email, args.password or os.environ.get("ADMIN_PASSWORD"))
    if args.command == "make-admin":
        return make_admin(args.email)
    if args.command == "add-balance":
        return add_balance(args.email, args.amount)
    if args.command == "set-password":
        password = args.password or ask_password("Новый пароль: ")
        return set_password(args.email, password) if password else 1
    if args.command == "backup-db":
        return backup_db(args.target_dir)
    if args.command == "purge":
        return purge(args.clicks_days)
    parser.print_help()  # запуск без команды — подсказка
    return 1


if __name__ == "__main__":
    sys.exit(main())
