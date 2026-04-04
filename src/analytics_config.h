#pragma once

#include <cstddef>

struct AnalyticsConfig {
    std::size_t trade_window_messages = 50;
    double vol_window_seconds = 60.0;
};
