#include "lob/replay.hpp"

#include <algorithm>
#include <chrono>
#include <unordered_map>
#include <utility>

namespace lob {
namespace {

struct EstimatedLevelState {
    AggregateQuantity total_size{0};
    OrderCount order_count{0};
};

struct EstimatedOrderState {
    Price price{0};
    Quantity remaining_size{0};
    Side side{Side::Buy};
};

struct BuildEstimate {
    std::size_t peak_active_orders{0};
    std::size_t peak_levels_per_side{0};
};

using EstimatedLevels = std::unordered_map<Price, EstimatedLevelState>;
using EstimatedOrders = std::unordered_map<OrderId, EstimatedOrderState>;

EstimatedLevels& estimated_levels_for(
    Side side,
    EstimatedLevels& bid_levels,
    EstimatedLevels& ask_levels) {
    return side == Side::Buy ? bid_levels : ask_levels;
}

void apply_estimated_reduction(
    EstimatedOrders::iterator order_it,
    Quantity reduction,
    EstimatedOrders& orders,
    EstimatedLevels& bid_levels,
    EstimatedLevels& ask_levels) {
    if (reduction <= 0) {
        return;
    }

    EstimatedOrderState& order = order_it->second;
    EstimatedLevels& levels = estimated_levels_for(order.side, bid_levels, ask_levels);
    auto level_it = levels.find(order.price);
    if (level_it == levels.end()) {
        orders.erase(order_it);
        return;
    }

    level_it->second.total_size -= reduction;

    if (reduction >= order.remaining_size) {
        if (level_it->second.order_count > 0) {
            --level_it->second.order_count;
        }
        if (level_it->second.total_size == 0 && level_it->second.order_count == 0) {
            levels.erase(level_it);
        }
        orders.erase(order_it);
        return;
    }

    order.remaining_size -= reduction;
    if (level_it->second.total_size == 0 && level_it->second.order_count == 0) {
        levels.erase(level_it);
    }
}

BuildEstimate estimate_build_requirements(const std::vector<LobsterMessage>& messages) {
    EstimatedOrders orders;
    orders.reserve(messages.size());
    EstimatedLevels bid_levels;
    EstimatedLevels ask_levels;

    BuildEstimate estimate;

    for (const LobsterMessage& message : messages) {
        switch (message.event_type) {
        case EventType::NewOrder: {
            if (message.size <= 0) {
                break;
            }

            auto existing = orders.find(message.order_id);
            if (existing != orders.end()) {
                apply_estimated_reduction(existing, existing->second.remaining_size, orders, bid_levels, ask_levels);
            }

            auto [order_it, inserted] = orders.emplace(
                message.order_id,
                EstimatedOrderState{message.price, message.size, message.direction});
            if (!inserted) {
                order_it->second = EstimatedOrderState{message.price, message.size, message.direction};
            }

            EstimatedLevels& levels = estimated_levels_for(message.direction, bid_levels, ask_levels);
            EstimatedLevelState& level_state = levels[message.price];
            level_state.total_size += message.size;
            ++level_state.order_count;
            break;
        }
        case EventType::PartialCancel:
        case EventType::ExecutionVisible: {
            if (message.size <= 0) {
                break;
            }

            auto order_it = orders.find(message.order_id);
            if (order_it == orders.end()) {
                break;
            }

            const Quantity reduction = std::min(order_it->second.remaining_size, message.size);
            apply_estimated_reduction(order_it, reduction, orders, bid_levels, ask_levels);
            break;
        }
        case EventType::FullCancel: {
            auto order_it = orders.find(message.order_id);
            if (order_it == orders.end()) {
                break;
            }
            apply_estimated_reduction(
                order_it,
                order_it->second.remaining_size,
                orders,
                bid_levels,
                ask_levels);
            break;
        }
        case EventType::ExecutionHidden:
        case EventType::CrossTrade:
        case EventType::TradingHalt:
            break;
        }

        estimate.peak_active_orders = std::max(estimate.peak_active_orders, orders.size());
        estimate.peak_levels_per_side = std::max(
            estimate.peak_levels_per_side,
            std::max(bid_levels.size(), ask_levels.size()));
    }

    return estimate;
}

}  // namespace

OrderBookBuildConfig derive_order_book_build_config(
    const std::vector<LobsterMessage>& messages,
    OrderBookBuildConfig config) {
    if (config.expected_orders > 0 && config.expected_levels_per_side > 0) {
        return config;
    }

    const BuildEstimate estimate = estimate_build_requirements(messages);
    if (config.expected_orders == 0) {
        config.expected_orders = estimate.peak_active_orders;
    }
    if (config.expected_levels_per_side == 0) {
        config.expected_levels_per_side = estimate.peak_levels_per_side;
    }
    return config;
}

void replay_messages(const std::vector<LobsterMessage>& messages, OrderBook& book) {
    for (const LobsterMessage& message : messages) {
        book.apply(message);
    }
}

ReplaySummary benchmark_replay(
    const std::vector<LobsterMessage>& messages,
    OrderBookBackend backend,
    std::size_t depth,
    std::size_t repeats,
    OrderBookBuildConfig config) {
    const std::size_t safe_repeats = repeats == 0 ? 1 : repeats;
    const OrderBookBuildConfig resolved_config = derive_order_book_build_config(messages, config);
    BookSnapshot final_snapshot;

    const auto start = std::chrono::steady_clock::now();
    for (std::size_t iteration = 0; iteration < safe_repeats; ++iteration) {
        std::unique_ptr<OrderBook> book = make_order_book(backend, resolved_config);
        replay_messages(messages, *book);
        if (iteration + 1 == safe_repeats) {
            final_snapshot = book->snapshot(depth);
        }
    }
    const auto end = std::chrono::steady_clock::now();

    const double elapsed_ms = std::chrono::duration<double, std::milli>(end - start).count();
    const double elapsed_seconds = elapsed_ms / 1000.0;
    const std::size_t processed_messages = messages.size() * safe_repeats;

    ReplaySummary summary;
    summary.backend = backend;
    summary.processed_messages = processed_messages;
    summary.repeats = safe_repeats;
    summary.elapsed_ms = elapsed_ms;
    summary.messages_per_second = elapsed_seconds > 0.0 ? processed_messages / elapsed_seconds : 0.0;
    summary.final_snapshot = std::move(final_snapshot);
    return summary;
}

}  // namespace lob
