#pragma once

#include "analytics_config.h"
#include "analytics_record.h"

#include <cstdint>
#include <deque>

class OrderBook;
struct LobsterMessage;

class AnalyticsEngine {
public:
    explicit AnalyticsEngine(AnalyticsConfig config = {});

    AnalyticsRecord onMessage(const LobsterMessage& message, const OrderBook& book);
    const AnalyticsConfig& config() const;

private:
    struct TradeEntry {
        double timestamp = 0.0;
        double price = 0.0;
        int64_t size = 0;
        int64_t signed_size = 0;
    };

    struct MidPoint {
        double timestamp = 0.0;
        double mid = 0.0;
    };

    void appendTrade(const LobsterMessage& message);
    void trimTradeWindow();
    void appendMid(double timestamp, double mid);
    void trimMidWindow(double current_timestamp);

    AnalyticsConfig config_;
    std::deque<TradeEntry> trades_;
    double trade_notional_sum_ = 0.0;
    int64_t trade_volume_sum_ = 0;
    int64_t signed_trade_volume_sum_ = 0;

    std::deque<MidPoint> mids_;
    double mid_return_square_sum_ = 0.0;
};
