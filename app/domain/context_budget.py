"""Лимит размера пакета контекста: оценка токенов и бюджет в байтах.

Оценка — `ceil(utf8_bytes / 4)`, **средняя, а не верхняя граница**: обычный
код даёт ~4 символа на токен, но код с плотной пунктуацией — больше токенов на
байт (в худшем случае токен на байт). Точное число считает только токенизатор
модели; он появится вместе с реальным `LlmGateway`, и там же — проверка, что
пакет, системный промпт и место под ответ помещаются в окно модели. Здесь
жёстко соблюдается сама оценка: она берётся от того самого сериализованного
тела, которое сохраняется и хэшируется, и `token_count` её не превышает.

Бюджет считает элементы массивов: стоимость элемента — длина его JSON плюс
запятая-разделитель. Каркас тела оплачивается отдельно, до элементов, поэтому
сумма сверху ограничивает итоговый размер: тело, собранное через бюджет,
никогда не превышает лимит в байтах.
"""

import json
import math
from typing import Any

BYTES_PER_TOKEN = 4


def serialize(value: Any) -> str:
    """Каноническая сериализация тела: компактно, ключи по порядку, UTF-8 как есть."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_size(value: Any) -> int:
    """Размер сериализованного значения в байтах UTF-8."""
    return len(serialize(value).encode("utf-8"))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / BYTES_PER_TOKEN)


class Budget:
    """Остаток байт под элементы массивов тела.

    Изменяемый счётчик внутри одной чистой сборки: наружу не утекает, время
    и I/O не трогает.
    """

    def __init__(self, limit_bytes: int) -> None:
        self.remaining = limit_bytes

    @staticmethod
    def cost(item: Any) -> int:
        # +1 — запятая между элементами массива при компактной сериализации.
        return json_size(item) + 1

    def take(self, item: Any) -> bool:
        """Оплатить элемент, если он помещается целиком; иначе ничего не списать."""
        price = self.cost(item)
        if price > self.remaining:
            return False
        self.remaining -= price
        return True

    def split(self, limit_bytes: int) -> Budget:
        """Выделить под-бюджет не больше `limit_bytes` из остатка.

        Неистраченное возвращается вызовом `merge`.
        """
        share = min(limit_bytes, self.remaining)
        self.remaining -= share
        return Budget(share)

    def merge(self, other: Budget) -> None:
        self.remaining += other.remaining
        other.remaining = 0
