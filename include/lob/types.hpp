#pragma once

#include <cmath>
#include <cstdint>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <string_view>

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

enum class ContainerType {
    StdMap,
    FlatVector,
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
    std::int64_t total_size;
    std::int32_t order_count;
    Side side;
};

struct OrderState {
    std::int64_t price;
    std::int32_t remaining_size;
    Side side;
};

struct BookSnapshot {
    std::int64_t best_bid{0};
    std::int64_t best_ask{0};
    std::int64_t spread{-1};
    double mid_price{std::numeric_limits<double>::quiet_NaN()};
    std::int64_t total_bid_size{0};
    std::int64_t total_ask_size{0};
    std::size_t bid_levels{0};
    std::size_t ask_levels{0};
    std::size_t active_levels{0};
    std::size_t total_orders{0};
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

inline std::string_view side_to_string(Side side) {
    switch (side) {
    case Side::Buy:
        return "buy";
    case Side::Sell:
        return "sell";
    }
    throw std::invalid_argument("invalid side");
}

inline std::string_view container_type_name(ContainerType type) {
    switch (type) {
    case ContainerType::StdMap:
        return "std::map";
    case ContainerType::FlatVector:
        return "flat_vec";
    }
    throw std::invalid_argument("invalid container type");
}

inline bool parse_container_type(std::string_view text, ContainerType& out) {
    if (text == "map" || text == "std::map") {
        out = ContainerType::StdMap;
        return true;
    }
    if (text == "flat" || text == "flat_vec" || text == "flat-vector") {
        out = ContainerType::FlatVector;
        return true;
    }
    return false;
}

}  // namespace lob
