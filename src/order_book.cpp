#include "order_book.h"

#include "lobster_parser.h"

#include <algorithm>
#include <iostream>

namespace {

template <typename LevelMap>
std::optional<PriceLevel> nthLevelFromMap(const LevelMap& levels, int n) {
    if (n < 0) {
        return std::nullopt;
    }

    auto it = levels.begin();
    for (int i = 0; i < n && it != levels.end(); ++i) {
        ++it;
    }

    if (it == levels.end()) {
        return std::nullopt;
    }

    return it->second;
}

}  // namespace

void OrderBook::processMessage(const LobsterMessage& msg) {
    const bool resume_indicator = msg.event_type == 7 && msg.price == 1;

    if (halted_ && msg.event_type == 1) {
        halted_ = false;
    } else if (halted_ && !resume_indicator) {
        std::cerr << "Warning: processing event type " << msg.event_type
                  << " while trading is halted\n";
    }

    switch (msg.event_type) {
        case 1:
            addOrder(msg.order_id, msg.direction, msg.price, msg.size);
            break;
        case 2:
            cancelOrder(msg.order_id, msg.size);
            break;
        case 3: {
            const auto order_it = orders_.find(msg.order_id);
            const int64_t cancel_size = order_it == orders_.end() ? msg.size : order_it->second.remaining;
            cancelOrder(msg.order_id, cancel_size);
            break;
        }
        case 4:
            executeOrder(msg.order_id, msg.size);
            break;
        case 5:
            ++hidden_exec_count_;
            break;
        case 6:
            ++cross_trade_count_;
            break;
        case 7:
            halted_ = msg.price != 1;
            break;
        default:
            std::cerr << "Warning: unknown event type " << msg.event_type << '\n';
            break;
    }
}

void OrderBook::addOrder(int64_t order_id, int side, int64_t price, int64_t size) {
    if (size <= 0) {
        return;
    }

    if (side != 1 && side != -1) {
        std::cerr << "Warning: invalid side " << side << " for order " << order_id << '\n';
        return;
    }

    const auto existing = orders_.find(order_id);
    if (existing != orders_.end()) {
        std::cerr << "Warning: replacing existing order_id " << order_id << '\n';
        applyReduction(order_id, existing->second.remaining, "replace");
    }

    orders_[order_id] = Order{order_id, price, size, side};

    if (side == 1) {
        auto& level = bids_[price];
        level.total_size += size;
        level.order_count += 1;
        return;
    }

    auto& level = asks_[price];
    level.total_size += size;
    level.order_count += 1;
}

void OrderBook::cancelOrder(int64_t order_id, int64_t size) {
    applyReduction(order_id, size, "cancel");
}

void OrderBook::executeOrder(int64_t order_id, int64_t size) {
    applyReduction(order_id, size, "execute");
}

std::optional<int64_t> OrderBook::bestBid() const {
    if (bids_.empty()) {
        return std::nullopt;
    }
    return bids_.begin()->first;
}

std::optional<int64_t> OrderBook::bestAsk() const {
    if (asks_.empty()) {
        return std::nullopt;
    }
    return asks_.begin()->first;
}

std::optional<PriceLevel> OrderBook::nthLevel(int side, int n) const {
    if (side == 1) {
        return nthLevelFromMap(bids_, n);
    }
    if (side == -1) {
        return nthLevelFromMap(asks_, n);
    }
    return std::nullopt;
}

const std::map<int64_t, PriceLevel, std::greater<int64_t>>& OrderBook::bids() const {
    return bids_;
}

const std::map<int64_t, PriceLevel>& OrderBook::asks() const {
    return asks_;
}

std::optional<Order> OrderBook::getOrder(int64_t order_id) const {
    const auto it = orders_.find(order_id);
    if (it == orders_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::size_t OrderBook::orderCount() const {
    return orders_.size();
}

std::size_t OrderBook::hiddenExecutionCount() const {
    return hidden_exec_count_;
}

std::size_t OrderBook::crossTradeCount() const {
    return cross_trade_count_;
}

std::size_t OrderBook::unknownIdCount() const {
    return unknown_id_count_;
}

bool OrderBook::halted() const {
    return halted_;
}

void OrderBook::applyReduction(int64_t order_id, int64_t size, const char* action) {
    if (size <= 0) {
        return;
    }

    const auto order_it = orders_.find(order_id);
    if (order_it == orders_.end()) {
        ++unknown_id_count_;
        std::cerr << "Warning: " << action << " for unknown order_id " << order_id << '\n';
        return;
    }

    Order& order = order_it->second;
    const int64_t delta = std::min(size, order.remaining);
    if (delta <= 0) {
        return;
    }

    if (order.side == 1) {
        auto level_it = bids_.find(order.price);
        if (level_it == bids_.end()) {
            std::cerr << "Warning: missing bid level for order_id " << order_id << '\n';
            orders_.erase(order_it);
            return;
        }

        order.remaining -= delta;
        level_it->second.total_size -= delta;
        if (order.remaining == 0) {
            level_it->second.order_count -= 1;
            orders_.erase(order_it);
        }
        if (level_it->second.order_count == 0) {
            bids_.erase(level_it);
        }
        return;
    }

    auto level_it = asks_.find(order.price);
    if (level_it == asks_.end()) {
        std::cerr << "Warning: missing ask level for order_id " << order_id << '\n';
        orders_.erase(order_it);
        return;
    }

    order.remaining -= delta;
    level_it->second.total_size -= delta;
    if (order.remaining == 0) {
        level_it->second.order_count -= 1;
        orders_.erase(order_it);
    }
    if (level_it->second.order_count == 0) {
        asks_.erase(level_it);
    }
}
