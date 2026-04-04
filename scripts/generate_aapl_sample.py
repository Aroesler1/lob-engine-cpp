#!/usr/bin/env python3

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path


SEED = 20240102
MESSAGE_COUNT = 12000
INITIAL_ADDS = 2500
BASE_PRICE = 1_500_000
PRICE_TICK = 100


@dataclass
class Order:
    side: int
    price: int
    remaining: int


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return repo_root() / "data"


def choose_active_order(active_orders: dict[int, Order], rng: random.Random) -> tuple[int, Order]:
    order_id = rng.choice(tuple(active_orders.keys()))
    return order_id, active_orders[order_id]


def bounded_mid_price(mid_price: int, rng: random.Random) -> int:
    mid_price += rng.randint(-2, 2) * PRICE_TICK
    lower = BASE_PRICE - 5_000
    upper = BASE_PRICE + 5_000
    return max(lower, min(upper, mid_price))


def make_add_price(side: int, mid_price: int, rng: random.Random) -> int:
    spread_ticks = rng.randint(1, 6)
    depth_ticks = rng.randint(0, 30)
    offset = (spread_ticks + depth_ticks) * PRICE_TICK
    if side == 1:
        return mid_price - offset
    return mid_price + offset


def write_synthetic_files() -> tuple[Path, Path]:
    rng = random.Random(SEED)
    out_dir = data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    message_path = out_dir / "AAPL_2024-01-02_message_10.csv"
    orderbook_path = out_dir / "AAPL_2024-01-02_orderbook_10.csv"

    active_orders: dict[int, Order] = {}
    next_order_id = 1_000_000
    timestamp = 34_200.0
    mid_price = BASE_PRICE
    rows: list[str] = []

    while len(rows) < MESSAGE_COUNT:
        mid_price = bounded_mid_price(mid_price, rng)
        timestamp += rng.uniform(0.0001, 0.0150)

        if len(rows) < INITIAL_ADDS or not active_orders:
            event_type = 1
        else:
            event_type = rng.choices(
                population=[1, 2, 3, 4, 5],
                weights=[45, 20, 10, 18, 7],
                k=1,
            )[0]

        if event_type == 1:
            side = 1 if rng.random() < 0.5 else -1
            price = make_add_price(side, mid_price, rng)
            size = rng.randint(10, 500)
            order_id = next_order_id
            next_order_id += 1
            active_orders[order_id] = Order(side=side, price=price, remaining=size)
            rows.append(
                f"{timestamp:.6f},{event_type},{order_id},{size},{price},{side}"
            )
            continue

        order_id, order = choose_active_order(active_orders, rng)

        if event_type == 2:
            size = rng.randint(1, max(1, order.remaining // 2))
            order.remaining -= size
            if order.remaining == 0:
                del active_orders[order_id]
            rows.append(
                f"{timestamp:.6f},{event_type},{order_id},{size},{order.price},{order.side}"
            )
            continue

        if event_type == 3:
            size = order.remaining
            del active_orders[order_id]
            rows.append(
                f"{timestamp:.6f},{event_type},{order_id},{size},{order.price},{order.side}"
            )
            continue

        size = rng.randint(1, order.remaining)
        order.remaining -= size
        if order.remaining == 0:
            del active_orders[order_id]
        rows.append(
            f"{timestamp:.6f},{event_type},{order_id},{size},{order.price},{order.side}"
        )

    message_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    orderbook_path.write_text("", encoding="utf-8")
    return message_path, orderbook_path


def main() -> None:
    message_path, orderbook_path = write_synthetic_files()
    print(f"Wrote {MESSAGE_COUNT} messages to {message_path}")
    print(f"Wrote placeholder order book file to {orderbook_path}")


if __name__ == "__main__":
    main()
