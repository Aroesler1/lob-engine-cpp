#pragma once

#include <cstdint>
#include <limits>

struct AnalyticsRecord {
    double timestamp = 0.0;
    double best_bid = std::numeric_limits<double>::quiet_NaN();
    double best_ask = std::numeric_limits<double>::quiet_NaN();
    double spread = std::numeric_limits<double>::quiet_NaN();
    double mid = std::numeric_limits<double>::quiet_NaN();
    int64_t bid_depth_1 = 0;
    int64_t ask_depth_1 = 0;
    int64_t bid_depth_5 = 0;
    int64_t ask_depth_5 = 0;
    int64_t bid_depth_10 = 0;
    int64_t ask_depth_10 = 0;
    double order_imbalance = 0.0;
    double rolling_vwap = std::numeric_limits<double>::quiet_NaN();
    double trade_flow_imbalance = 0.0;
    double rolling_realized_vol = std::numeric_limits<double>::quiet_NaN();
};
