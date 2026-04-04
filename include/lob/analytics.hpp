#pragma once

#include <cstddef>
#include <cstdint>
#include <ostream>
#include <string>
#include <vector>

#include "lob/types.hpp"

namespace lob {

struct AnalyticsRow {
    std::size_t sequence{0};
    double timestamp{0.0};
    int event_type{0};
    std::int64_t order_id{0};
    std::int32_t message_size{0};
    std::int64_t message_price{0};
    int direction{0};
    std::int64_t best_bid{0};
    std::int64_t best_ask{0};
    std::int64_t spread{-1};
    double mid_price{0.0};
    std::int64_t total_bid_size{0};
    std::int64_t total_ask_size{0};
    std::size_t total_orders{0};
    std::size_t bid_levels{0};
    std::size_t ask_levels{0};
    std::size_t active_levels{0};
};

void write_analytics_csv(std::ostream& output, const std::vector<AnalyticsRow>& rows);
bool write_analytics_csv_file(const std::string& filepath, const std::vector<AnalyticsRow>& rows);
bool analytics_rows_equal(const AnalyticsRow& lhs, const AnalyticsRow& rhs, double epsilon = 1e-9);

}  // namespace lob
