"""Зачисление на баланс аккаунта администратором: `python -m app.worker.credit_balance`.

Помимо пополнения через Stripe (change `stripe-topup`): корректировки, возвраты, стартовые
балансы (design D8 change `account-balance`). Запускается в
образе воркера — `scripts/` туда не копируется — с теми же настройками окружения:

    python -m app.worker.credit_balance "$(gh api users/alice --jq .id)" 5 --note "оплата счёта 42" [--by admin]

Получатель — числовой id GitHub, а не логин: логин GitHub может перейти к другому человеку.
Отрицательная сумма — возврат или корректировка. Печатает логин получателя и новый баланс;
неизвестный id или сумма не число — код возврата 1, журнал не меняется.
"""

import argparse
import getpass
import sys
from datetime import UTC, datetime

from app.application.use_cases.billing import CreditRefusal, credit_balance
from app.config import load_settings
from app.domain.ids import new_id
from app.infrastructure.container import build_container

_REFUSALS = {
    CreditRefusal.UNKNOWN_ACCOUNT: (
        "аккаунта с таким id GitHub нет среди пользователей сервиса: нужен числовой id "
        "(gh api users/<логин> --jq .id), и пользователь должен хотя бы раз войти"
    ),
    CreditRefusal.NOT_A_NUMBER: "сумма — не число в евро с точкой и не больше 10 знаков после неё",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker.credit_balance", description="Зачислить деньги на баланс аккаунта сервиса.")
    parser.add_argument("github_id", help="числовой id пользователя GitHub: gh api users/<логин> --jq .id")
    parser.add_argument("amount", help="сумма в евро, например 5 или -0.5")
    parser.add_argument("--note", required=True, help="за что зачисление: попадает в журнал")
    parser.add_argument("--by", default=getpass.getuser(), help="кто зачислил; по умолчанию пользователь ОС")
    args = parser.parse_args(argv)
    uow = build_container(load_settings()).unit_of_work()
    outcome = credit_balance(
        uow, args.github_id, args.amount, note=args.note, credited_by=args.by, now=datetime.now(UTC), new_id=new_id
    )
    if isinstance(outcome, CreditRefusal):
        print(f"не зачислено: {_REFUSALS[outcome]}", file=sys.stderr)
        return 1
    print(f"баланс {outcome.login} (id GitHub {args.github_id}): {outcome.balance.normalize():f} €")
    return 0


if __name__ == "__main__":
    sys.exit(main())
