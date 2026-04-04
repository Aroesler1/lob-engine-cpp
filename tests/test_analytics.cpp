#include <cassert>
#include <filesystem>
#include <iostream>
#include <vector>

#include "lob/analytics.hpp"
#include "lob/order_book.hpp"
#include "lob/parser.hpp"

namespace {

std::filesystem::path source_root() {
    return std::filesystem::path(LOB_ENGINE_SOURCE_DIR);
}

void test_analytics_rows_cover_every_message() {
    const auto sample_path = source_root() / "data" / "sample_messages.csv";
    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(sample_path.string());

    lob::MapOrderBook book;
    const std::vector<lob::AnalyticsRow> rows = lob::replay_with_analytics(messages, book);

    assert(rows.size() == messages.size());
    assert(rows.front().timestamp == messages.front().timestamp);
    assert(rows.back().timestamp == messages.back().timestamp);
    assert(rows.back().bid_depth_1 >= 0);
    assert(rows.back().ask_depth_1 >= 0);
}

void test_trade_metrics_and_realized_vol_are_populated() {
    std::vector<lob::LobsterMessage> messages = {
        {100.0, lob::EventType::NewOrder, 1, 50, 10000, lob::Side::Buy},
        {100.1, lob::EventType::NewOrder, 2, 60, 10100, lob::Side::Sell},
        {100.2, lob::EventType::ExecutionVisible, 1, 10, 10000, lob::Side::Buy},
        {100.3, lob::EventType::ExecutionVisible, 2, 12, 10100, lob::Side::Sell},
        {100.4, lob::EventType::NewOrder, 3, 30, 10050, lob::Side::Buy},
    };

    lob::MapOrderBook book;
    const std::vector<lob::AnalyticsRow> rows = lob::replay_with_analytics(messages, book);

    assert(rows.size() == messages.size());
    assert(rows[2].rolling_vwap.has_value());
    assert(rows[2].trade_flow_imbalance.has_value());
    assert(rows.back().rolling_realized_vol.has_value());
}

}  // namespace

int main() {
    test_analytics_rows_cover_every_message();
    test_trade_metrics_and_realized_vol_are_populated();
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
