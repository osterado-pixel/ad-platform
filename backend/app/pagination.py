"""Постраничная выдача списков.

- limit — размер страницы (у каждого эндпоинта свой максимум).
- offset — сдвиг; ограничен MAX_OFFSET: OFFSET 900000 заставляет БД прочитать и выбросить
  900 тыс. строк. Для глубокой прокрутки — курсор before_id.
- before_id — курсор: «записи с id меньше этого». Следующая страница берётся по индексу
  за одинаковое время на любой глубине (keyset-пагинация).

Признак «есть ещё» отдаётся в заголовке X-Has-More (а курсор следующей страницы — в
X-Next-Before-Id): считать COUNT(*) по миллионам строк на каждую страницу — тоже нагрузка.
"""
from fastapi import Query, Response
from sqlalchemy import Select
from sqlalchemy.orm import Session

MAX_OFFSET = 10_000

# Заголовки, которые фронтенд на другом домене должен иметь право прочитать (CORS expose_headers)
PAGINATION_HEADERS = ["X-Has-More", "X-Next-Before-Id"]


def limit_param(default: int = 50, maximum: int = 200):
    return Query(default=default, ge=1, le=maximum, description=f"Размер страницы (1–{maximum})")


def offset_param():
    return Query(default=0, ge=0, le=MAX_OFFSET,
                 description=f"Сдвиг (до {MAX_OFFSET}). Для дальнейшей прокрутки — before_id")


def before_id_param():
    return Query(default=None, ge=1, description="Курсор: вернуть записи с id меньше этого "
                                                 "(значение из заголовка X-Next-Before-Id)")


def fetch_page(db: Session, stmt: Select, response: Response, limit: int, offset: int = 0,
               *, cursor_attr: str | None = None) -> list:
    """Выполняет запрос с limit+1 строк: лишняя строка значит «есть следующая страница»."""
    rows = db.scalars(stmt.limit(limit + 1).offset(offset)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    response.headers["X-Has-More"] = "true" if has_more else "false"
    if has_more and cursor_attr and rows:
        response.headers["X-Next-Before-Id"] = str(getattr(rows[-1], cursor_attr))
    return rows
