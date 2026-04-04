#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

#include "lob/types.hpp"

namespace lob {

template <Side SideValue>
struct PriceBefore {
    bool operator()(std::int64_t lhs, std::int64_t rhs) const noexcept {
        if constexpr (SideValue == Side::Buy) {
            return lhs > rhs;
        }
        return lhs < rhs;
    }
};

template <Side SideValue>
class MapLevelContainer {
public:
    void reserve(std::size_t) noexcept {}

    void apply_delta(std::int64_t price, std::int32_t size_delta, int order_delta) {
        const auto it = levels_.find(price);
        const std::int64_t previous_size = (it == levels_.end()) ? 0 : it->second.total_size;
        const int previous_orders = (it == levels_.end()) ? 0 : it->second.order_count;
        const std::int64_t new_size = previous_size + static_cast<std::int64_t>(size_delta);
        const int new_orders = previous_orders + order_delta;

        if (new_size <= 0 || new_orders <= 0) {
            if (it != levels_.end()) {
                total_visible_size_ -= previous_size;
                levels_.erase(it);
            }
            return;
        }

        if (it == levels_.end()) {
            OrderBookLevel level{};
            level.price = price;
            level.total_size = new_size;
            level.order_count = static_cast<std::int32_t>(new_orders);
            level.side = SideValue;
            levels_.emplace(price, level);
            total_visible_size_ += new_size;
            return;
        }

        total_visible_size_ += (new_size - previous_size);
        it->second.total_size = new_size;
        it->second.order_count = static_cast<std::int32_t>(new_orders);
    }

    const OrderBookLevel* best_level() const noexcept {
        if (levels_.empty()) {
            return nullptr;
        }
        return &levels_.begin()->second;
    }

    std::size_t level_count() const noexcept {
        return levels_.size();
    }

    std::int64_t total_visible_size() const noexcept {
        return total_visible_size_;
    }

    std::size_t estimate_memory_bytes() const noexcept {
        constexpr std::size_t node_overhead = sizeof(void*) * 4 + sizeof(bool);
        return sizeof(*this) + levels_.size() * (sizeof(typename MapType::value_type) + node_overhead);
    }

private:
    using MapType = std::map<std::int64_t, OrderBookLevel, PriceBefore<SideValue>>;

    MapType levels_;
    std::int64_t total_visible_size_{0};
};

template <Side SideValue>
class FlatLevelContainer {
public:
    void reserve(std::size_t count) {
        levels_.reserve(count);
    }

    void apply_delta(std::int64_t price, std::int32_t size_delta, int order_delta) {
        const auto it = lower_bound(price);
        const bool found = (it != levels_.end() && it->price == price);
        const std::int64_t previous_size = found ? it->total_size : 0;
        const int previous_orders = found ? it->order_count : 0;
        const std::int64_t new_size = previous_size + static_cast<std::int64_t>(size_delta);
        const int new_orders = previous_orders + order_delta;

        if (new_size <= 0 || new_orders <= 0) {
            if (found) {
                total_visible_size_ -= previous_size;
                levels_.erase(it);
            }
            return;
        }

        if (!found) {
            OrderBookLevel level{};
            level.price = price;
            level.total_size = new_size;
            level.order_count = static_cast<std::int32_t>(new_orders);
            level.side = SideValue;
            levels_.insert(it, level);
            total_visible_size_ += new_size;
            return;
        }

        total_visible_size_ += (new_size - previous_size);
        it->total_size = new_size;
        it->order_count = static_cast<std::int32_t>(new_orders);
    }

    const OrderBookLevel* best_level() const noexcept {
        if (levels_.empty()) {
            return nullptr;
        }
        return &levels_.front();
    }

    std::size_t level_count() const noexcept {
        return levels_.size();
    }

    std::int64_t total_visible_size() const noexcept {
        return total_visible_size_;
    }

    std::size_t estimate_memory_bytes() const noexcept {
        return sizeof(*this) + levels_.capacity() * sizeof(OrderBookLevel);
    }

private:
    using Iterator = typename std::vector<OrderBookLevel>::iterator;

    Iterator lower_bound(std::int64_t price) {
        return std::lower_bound(
            levels_.begin(),
            levels_.end(),
            price,
            [](const OrderBookLevel& level, std::int64_t target_price) {
                if constexpr (SideValue == Side::Buy) {
                    return level.price > target_price;
                }
                return level.price < target_price;
            });
    }

    std::vector<OrderBookLevel> levels_;
    std::int64_t total_visible_size_{0};
};

}  // namespace lob
