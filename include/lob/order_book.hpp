#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <unordered_map>

#include "lob/analytics.hpp"
#include "lob/types.hpp"

namespace lob {

template <typename BidLevels, typename AskLevels>
class OrderBook {
public:
    void reserve_orders(std::size_t count) {
        orders_.reserve(count);
        const std::size_t per_side_hint = std::max<std::size_t>(count / 4, 8);
        bids_.reserve(per_side_hint);
        asks_.reserve(per_side_hint);
    }

    void process(const LobsterMessage& message) {
        switch (message.event_type) {
        case EventType::NewOrder:
            add_order(message);
            break;
        case EventType::PartialCancel:
            reduce_order(message.order_id, message.size);
            break;
        case EventType::FullCancel:
            remove_order(message.order_id);
            break;
        case EventType::ExecutionVisible:
            reduce_order(message.order_id, message.size);
            break;
        case EventType::ExecutionHidden:
        case EventType::CrossTrade:
        case EventType::TradingHalt:
            break;
        }

        peak_active_levels_ = std::max(peak_active_levels_, active_levels());
        peak_visible_orders_ = std::max(peak_visible_orders_, orders_.size());
    }

    BookSnapshot snapshot() const {
        BookSnapshot out{};
        const OrderBookLevel* best_bid = bids_.best_level();
        const OrderBookLevel* best_ask = asks_.best_level();

        out.best_bid = (best_bid == nullptr) ? 0 : best_bid->price;
        out.best_ask = (best_ask == nullptr) ? 0 : best_ask->price;
        out.total_bid_size = bids_.total_visible_size();
        out.total_ask_size = asks_.total_visible_size();
        out.bid_levels = bids_.level_count();
        out.ask_levels = asks_.level_count();
        out.active_levels = out.bid_levels + out.ask_levels;
        out.total_orders = orders_.size();

        if (best_bid != nullptr && best_ask != nullptr) {
            out.spread = out.best_ask - out.best_bid;
            out.mid_price = static_cast<double>(out.best_bid + out.best_ask) / 2.0;
        }

        return out;
    }

    AnalyticsRow analytics_row(std::size_t sequence, const LobsterMessage& message) const {
        const BookSnapshot snap = snapshot();

        AnalyticsRow row{};
        row.sequence = sequence;
        row.timestamp = message.timestamp;
        row.event_type = static_cast<int>(message.event_type);
        row.order_id = message.order_id;
        row.message_size = message.size;
        row.message_price = message.price;
        row.direction = (message.direction == Side::Buy) ? 1 : -1;
        row.best_bid = snap.best_bid;
        row.best_ask = snap.best_ask;
        row.spread = snap.spread;
        row.mid_price = snap.mid_price;
        row.total_bid_size = snap.total_bid_size;
        row.total_ask_size = snap.total_ask_size;
        row.total_orders = snap.total_orders;
        row.bid_levels = snap.bid_levels;
        row.ask_levels = snap.ask_levels;
        row.active_levels = snap.active_levels;
        return row;
    }

    std::size_t peak_active_levels() const noexcept {
        return peak_active_levels_;
    }

    std::size_t peak_visible_orders() const noexcept {
        return peak_visible_orders_;
    }

    std::size_t total_orders() const noexcept {
        return orders_.size();
    }

    std::size_t active_levels() const noexcept {
        return bids_.level_count() + asks_.level_count();
    }

    std::size_t approximate_memory_bytes() const noexcept {
        const std::size_t buckets = orders_.bucket_count() * sizeof(void*);
        const std::size_t nodes = orders_.size() * (sizeof(typename OrderMap::value_type) + sizeof(void*) * 2);
        return sizeof(*this) + buckets + nodes + bids_.estimate_memory_bytes() + asks_.estimate_memory_bytes();
    }

private:
    using OrderMap = std::unordered_map<std::int64_t, OrderState>;

    void add_order(const LobsterMessage& message) {
        if (message.size <= 0) {
            return;
        }

        auto existing = orders_.find(message.order_id);
        if (existing != orders_.end()) {
            remove_existing(existing->second);
            orders_.erase(existing);
        }

        apply_delta(message.direction, message.price, message.size, 1);
        orders_.emplace(message.order_id, OrderState{message.price, message.size, message.direction});
    }

    void reduce_order(std::int64_t order_id, std::int32_t reduction_size) {
        if (reduction_size <= 0) {
            return;
        }

        auto it = orders_.find(order_id);
        if (it == orders_.end()) {
            return;
        }

        const std::int32_t removed_size = std::min(it->second.remaining_size, reduction_size);
        const bool erase_order = (removed_size >= it->second.remaining_size);
        apply_delta(it->second.side, it->second.price, -removed_size, erase_order ? -1 : 0);

        if (erase_order) {
            orders_.erase(it);
            return;
        }

        it->second.remaining_size -= removed_size;
    }

    void remove_order(std::int64_t order_id) {
        auto it = orders_.find(order_id);
        if (it == orders_.end()) {
            return;
        }

        remove_existing(it->second);
        orders_.erase(it);
    }

    void remove_existing(const OrderState& order) {
        apply_delta(order.side, order.price, -order.remaining_size, -1);
    }

    void apply_delta(Side side, std::int64_t price, std::int32_t size_delta, int order_delta) {
        if (side == Side::Buy) {
            bids_.apply_delta(price, size_delta, order_delta);
            return;
        }
        asks_.apply_delta(price, size_delta, order_delta);
    }

    OrderMap orders_;
    BidLevels bids_;
    AskLevels asks_;
    std::size_t peak_active_levels_{0};
    std::size_t peak_visible_orders_{0};
};

}  // namespace lob
