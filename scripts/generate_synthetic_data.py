#!/usr/bin/env python3
"""Generate synthetic LOBSTER-format message streams for benchmark comparisons.

The generated MSFT/TSLA/SPY files are synthetic and are intended only for
comparative benchmark workloads. Prices use the same 1/10000-dollar convention
as the existing sample data in this repository.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path


PRICE_SCALE = 10_000
CENT = 100
START_TIME = 34_200.0
TRADING_SECONDS = 23_400.0


@dataclass(frozen=True)
class TickerConfig:
    name: str
    message_count: int
    base_price: int
    spread_min: int
    spread_max: int
    target_levels: int
    target_orders: int
    center_shift_limit: int
    volatility_sigma_ticks: float
    min_size: int
    max_size: int
    seed: int


class OrderPool:
    def __init__(self) -> None:
        self.orders: dict[int, dict[str, int]] = {}
        self.order_ids: list[int] = []
        self.positions: dict[int, int] = {}
        self.buy_levels: dict[int, int] = {}
        self.sell_levels: dict[int, int] = {}

    def __len__(self) -> int:
        return len(self.orders)

    def active_levels(self) -> int:
        return len(self.buy_levels) + len(self.sell_levels)

    def add(self, order_id: int, side: int, price: int, size: int) -> None:
        self.orders[order_id] = {"side": side, "price": price, "size": size}
        self.positions[order_id] = len(self.order_ids)
        self.order_ids.append(order_id)
        levels = self.buy_levels if side == 1 else self.sell_levels
        levels[price] = levels.get(price, 0) + 1

    def random_order(self, rng: random.Random) -> tuple[int, dict[str, int]]:
        order_id = self.order_ids[rng.randrange(len(self.order_ids))]
        return order_id, self.orders[order_id]

    def reduce(self, order_id: int, reduction: int) -> int:
        order = self.orders[order_id]
        removed = min(order["size"], reduction)
        order["size"] -= removed
        if order["size"] == 0:
            self.delete(order_id)
        return removed

    def delete(self, order_id: int) -> dict[str, int]:
        order = self.orders.pop(order_id)
        levels = self.buy_levels if order["side"] == 1 else self.sell_levels
        levels[order["price"]] -= 1
        if levels[order["price"]] == 0:
            del levels[order["price"]]

        idx = self.positions.pop(order_id)
        last_id = self.order_ids.pop()
        if last_id != order_id:
            self.order_ids[idx] = last_id
            self.positions[last_id] = idx
        return order


def sample_price(config: TickerConfig, rng: random.Random, mid_price: int, side: int) -> int:
    spread = rng.randrange(config.spread_min, config.spread_max + CENT, CENT)
    best_bid = mid_price - spread // 2
    best_bid = max(best_bid - (best_bid % CENT), 10 * PRICE_SCALE)
    best_ask = best_bid + spread
    side_levels = max(config.target_levels // 2, 1)
    depth_offset = min(int(rng.expovariate(1.0 / max(side_levels / 3.0, 1.0))), side_levels - 1)
    if side == 1:
        return max(best_bid - depth_offset * CENT, CENT)
    return best_ask + depth_offset * CENT


def pick_event_type(config: TickerConfig, pool: OrderPool, rng: random.Random) -> int:
    if len(pool) < config.target_orders * 0.75 or pool.active_levels() < config.target_levels * 0.7:
        weights = [(1, 0.78), (2, 0.08), (3, 0.05), (4, 0.05), (5, 0.04)]
    elif len(pool) > config.target_orders * 1.2 or pool.active_levels() > config.target_levels * 1.2:
        weights = [(1, 0.38), (2, 0.22), (3, 0.18), (4, 0.16), (5, 0.06)]
    else:
        weights = [(1, 0.58), (2, 0.15), (3, 0.10), (4, 0.12), (5, 0.05)]

    threshold = rng.random()
    cumulative = 0.0
    for event_type, weight in weights:
        cumulative += weight
        if threshold <= cumulative:
            return event_type
    return weights[-1][0]


def format_message(timestamp: float, event_type: int, order_id: int, size: int, price: int, side: int) -> str:
    return f"{timestamp:.6f},{event_type},{order_id},{size},{price},{side}\n"


def generate_messages(config: TickerConfig) -> list[str]:
    rng = random.Random(config.seed)
    pool = OrderPool()
    next_order_id = 1_000_000
    timestamp = START_TIME
    mid_offset = 0
    mean_gap = TRADING_SECONDS / config.message_count
    messages: list[str] = []

    for _ in range(config.message_count):
        timestamp += rng.expovariate(1.0 / mean_gap)
        timestamp = min(timestamp, START_TIME + TRADING_SECONDS)
        mid_offset += int(round(rng.gauss(0.0, config.volatility_sigma_ticks))) * CENT
        mid_offset = max(-config.center_shift_limit, min(config.center_shift_limit, mid_offset))
        mid_price = config.base_price + mid_offset
        mid_price = max(mid_price, 10 * PRICE_SCALE)

        event_type = pick_event_type(config, pool, rng)
        if len(pool) == 0 and event_type != 1:
            event_type = 1

        if event_type == 1:
            side = 1 if rng.random() < 0.5 else -1
            size = rng.randint(config.min_size, config.max_size)
            price = sample_price(config, rng, mid_price, side)
            order_id = next_order_id
            next_order_id += 1
            pool.add(order_id, side, price, size)
            messages.append(format_message(timestamp, 1, order_id, size, price, side))
            continue

        if event_type == 5:
            side = 1 if rng.random() < 0.5 else -1
            size = rng.randint(config.min_size, config.max_size)
            price = sample_price(config, rng, mid_price, side)
            order_id = next_order_id
            next_order_id += 1
            messages.append(format_message(timestamp, 5, order_id, size, price, side))
            continue

        order_id, order = pool.random_order(rng)
        if event_type == 2:
            reduction = max(1, int(math.ceil(order["size"] * rng.uniform(0.1, 0.6))))
            removed = pool.reduce(order_id, reduction)
            messages.append(
                format_message(timestamp, 2, order_id, removed, order["price"], order["side"])
            )
            continue

        if event_type == 3:
            deleted = pool.delete(order_id)
            messages.append(
                format_message(timestamp, 3, order_id, deleted["size"], deleted["price"], deleted["side"])
            )
            continue

        executed = max(1, int(math.ceil(order["size"] * rng.uniform(0.2, 1.0))))
        removed = pool.reduce(order_id, executed)
        messages.append(format_message(timestamp, 4, order_id, removed, order["price"], order["side"]))

    return messages


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    configs = [
        TickerConfig(
            name="MSFT_synthetic",
            message_count=50_000,
            base_price=330 * PRICE_SCALE,
            spread_min=1 * CENT,
            spread_max=2 * CENT,
            target_levels=50,
            target_orders=260,
            center_shift_limit=2 * CENT,
            volatility_sigma_ticks=0.30,
            min_size=50,
            max_size=500,
            seed=11,
        ),
        TickerConfig(
            name="TSLA_synthetic",
            message_count=100_000,
            base_price=240 * PRICE_SCALE,
            spread_min=5 * CENT,
            spread_max=10 * CENT,
            target_levels=100,
            target_orders=650,
            center_shift_limit=8 * CENT,
            volatility_sigma_ticks=1.00,
            min_size=20,
            max_size=300,
            seed=22,
        ),
        TickerConfig(
            name="SPY_synthetic",
            message_count=200_000,
            base_price=510 * PRICE_SCALE,
            spread_min=1 * CENT,
            spread_max=1 * CENT,
            target_levels=200,
            target_orders=2_400,
            center_shift_limit=4 * CENT,
            volatility_sigma_ticks=0.18,
            min_size=100,
            max_size=1000,
            seed=33,
        ),
    ]

    for config in configs:
        output_path = data_dir / f"{config.name}_message.csv"
        output_path.write_text("".join(generate_messages(config)), encoding="utf-8")
        print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
