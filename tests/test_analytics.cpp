#include <cassert>
#include <filesystem>
#include <iostream>
#include <vector>

#include "lob/analytics.hpp"
#include "lob/order_book.hpp"
#include "lob/parser.hpp"
#include "lob/replay.hpp"

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

void assert_rows_match(const lob::AnalyticsRow& lhs, const lob::AnalyticsRow& rhs) {
    assert(lhs.timestamp == rhs.timestamp);
    assert(lhs.best_bid == rhs.best_bid);
    assert(lhs.best_ask == rhs.best_ask);
    assert(lhs.spread == rhs.spread);
    assert(lhs.mid_price == rhs.mid_price);
    assert(lhs.bid_depth_1 == rhs.bid_depth_1);
    assert(lhs.bid_depth_5 == rhs.bid_depth_5);
    assert(lhs.bid_depth_10 == rhs.bid_depth_10);
    assert(lhs.ask_depth_1 == rhs.ask_depth_1);
    assert(lhs.ask_depth_5 == rhs.ask_depth_5);
    assert(lhs.ask_depth_10 == rhs.ask_depth_10);
    assert(lhs.order_imbalance == rhs.order_imbalance);
    assert(lhs.rolling_vwap == rhs.rolling_vwap);
    assert(lhs.trade_flow_imbalance == rhs.trade_flow_imbalance);
    assert(lhs.rolling_realized_vol == rhs.rolling_realized_vol);
}

void test_analytics_outputs_match_across_backends() {
    const auto sample_path = source_root() / "data" / "sample_messages.csv";
    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(sample_path.string());
    const lob::OrderBookBuildConfig build_config = lob::derive_order_book_build_config(messages);

    lob::MapOrderBook map_book(build_config);
    lob::FlatVectorOrderBook flat_book(build_config);
    const std::vector<lob::AnalyticsRow> map_rows = lob::replay_with_analytics(messages, map_book);
    const std::vector<lob::AnalyticsRow> flat_rows = lob::replay_with_analytics(messages, flat_book);

    assert(map_rows.size() == flat_rows.size());
    for (std::size_t index = 0; index < map_rows.size(); ++index) {
        assert_rows_match(map_rows[index], flat_rows[index]);
    }

    assert(map_book.snapshot(10) == flat_book.snapshot(10));
}

}  // namespace

int main() {
    test_analytics_rows_cover_every_message();
    test_trade_metrics_and_realized_vol_are_populated();
    test_analytics_outputs_match_across_backends();
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
