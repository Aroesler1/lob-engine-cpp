#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>

namespace lob {

using OrderId = std::int64_t;
using Price = std::int64_t;
using Quantity = std::int32_t;
using AggregateQuantity = std::int64_t;
using OrderCount = std::size_t;

enum class Side {
    Buy,
    Sell,
};

enum class EventType : int {
    NewOrder = 1,
    PartialCancel = 2,
    FullCancel = 3,
    ExecutionVisible = 4,
    ExecutionHidden = 5,
    CrossTrade = 6,
    TradingHalt = 7,
};

struct LobsterMessage {
    double timestamp;
    EventType event_type;
    OrderId order_id;
    Quantity size;
    Price price;
    Side direction;
};

struct OrderBookLevel {
    Price price;
    AggregateQuantity total_size;
    OrderCount order_count;
    Side side;
};

inline bool operator==(const OrderBookLevel& lhs, const OrderBookLevel& rhs) noexcept {
    return lhs.price == rhs.price &&
           lhs.total_size == rhs.total_size &&
           lhs.order_count == rhs.order_count &&
           lhs.side == rhs.side;
}

inline Side direction_to_side(int dir) {
    switch (dir) {
    case 1:
        return Side::Buy;
    case -1:
        return Side::Sell;
    default:
        throw std::invalid_argument("invalid LOBSTER direction");
    }
}

inline EventType int_to_event_type(int val) {
    if (val < 1 || val > 7) {
        throw std::invalid_argument("invalid LOBSTER event type");
    }
    return static_cast<EventType>(val);
}

}  // namespace lob
