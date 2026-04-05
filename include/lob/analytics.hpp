#pragma once

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "lob/order_book.hpp"
#include "lob/types.hpp"

namespace lob {

struct AnalyticsConfig {
    std::size_t trade_window_messages{1000};
    double realized_vol_window_seconds{300.0};
    std::size_t depth_levels{10};
    std::size_t expected_messages{0};
};

struct AnalyticsRow {
    double timestamp{0.0};
    std::optional<Price> best_bid;
    std::optional<Price> best_ask;
    std::optional<Price> spread;
    std::optional<double> mid_price;
    AggregateQuantity bid_depth_1{0};
    AggregateQuantity bid_depth_5{0};
    AggregateQuantity bid_depth_10{0};
    AggregateQuantity ask_depth_1{0};
    AggregateQuantity ask_depth_5{0};
    AggregateQuantity ask_depth_10{0};
    std::optional<double> order_imbalance;
    std::optional<double> rolling_vwap;
    std::optional<double> trade_flow_imbalance;
    std::optional<double> rolling_realized_vol;
};

class AnalyticsEngine {
public:
    explicit AnalyticsEngine(AnalyticsConfig config = {});
    ~AnalyticsEngine();

    AnalyticsEngine(AnalyticsEngine&&) noexcept;
    AnalyticsEngine& operator=(AnalyticsEngine&&) noexcept;

    AnalyticsEngine(const AnalyticsEngine&) = delete;
    AnalyticsEngine& operator=(const AnalyticsEngine&) = delete;

    AnalyticsRow on_message(const LobsterMessage& message, const BookSnapshot& snapshot);
    void on_message(const LobsterMessage& message, const BookSnapshot& snapshot, AnalyticsRow& row);
    void reset();

private:
    AnalyticsConfig config_;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

std::vector<AnalyticsRow> replay_with_analytics(
    const std::vector<LobsterMessage>& messages,
    OrderBook& book,
    AnalyticsConfig config = {});

void write_analytics_csv(const std::vector<AnalyticsRow>& rows, const std::string& output_path);

}  // namespace lob
