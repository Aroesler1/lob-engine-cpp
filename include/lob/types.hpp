#pragma once

#include <cstdint>
#include <stdexcept>

namespace lob {

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
    std::int64_t order_id;
    std::int32_t size;
    std::int64_t price;
    Side direction;
};

struct OrderBookLevel {
    std::int64_t price;
    std::int32_t total_size;
    std::int32_t order_count;
    Side side;
};

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
