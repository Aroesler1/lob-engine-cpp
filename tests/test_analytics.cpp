#include <cassert>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <optional>
#include <string>
#include <vector>

#include "lob/analytics.hpp"
#include "lob/order_book.hpp"
#include "lob/parser.hpp"
#include "lob/replay.hpp"

namespace {

std::filesystem::path source_root() {
    return std::filesystem::path(LOB_ENGINE_SOURCE_DIR);
}

std::filesystem::path make_temp_file(const std::string& stem) {
    static int counter = 0;
    return std::filesystem::temp_directory_path() /
           (stem + "_" + std::to_string(counter++) + ".csv");
}

bool almost_equal(double lhs, double rhs, double tolerance = 1e-9) {
    return std::fabs(lhs - rhs) <= tolerance;
}

lob::PredictionSnapshot make_prediction_snapshot(
    std::size_t message_index,
    std::optional<double> mid_price,
    std::optional<double> order_imbalance_top5) {
    return lob::PredictionSnapshot{message_index, mid_price, order_imbalance_top5};
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

// The rolling realized-vol sum is maintained incrementally (add on push,
// subtract on eviction) because recomputing it per message is O(window) and so
// O(n^2) over a session. This pins the incremental result to an independent
// recomputation from scratch AFTER the window has evicted samples -- eviction
// is the half an incremental accumulator gets wrong.
void test_incremental_realized_vol_matches_recomputation() {
    lob::AnalyticsConfig config;
    config.realized_vol_window_seconds = 5.0;

    std::vector<lob::LobsterMessage> messages;
    for (int i = 0; i < 40; ++i) {
        const lob::Price bid = 10000 + (i % 7) * 10;
        const lob::Price ask = bid + 20;
        const double t = 100.0 + static_cast<double>(i) * 0.5;  // 20s span vs 5s window
        messages.push_back({t, lob::EventType::NewOrder, 2 * i + 1, 50, bid, lob::Side::Buy});
        messages.push_back({t, lob::EventType::NewOrder, 2 * i + 2, 50, ask, lob::Side::Sell});
    }

    lob::MapOrderBook book;
    const std::vector<lob::AnalyticsRow> rows = lob::replay_with_analytics(messages, book, config);
    assert(rows.size() == messages.size());

    // rebuild the surviving window from the emitted mids, then sum directly
    const double final_ts = messages.back().timestamp;
    std::vector<double> window_mids;
    for (std::size_t i = 0; i < rows.size(); ++i) {
        if (rows[i].mid_price.has_value() &&
            messages[i].timestamp >= final_ts - config.realized_vol_window_seconds) {
            window_mids.push_back(*rows[i].mid_price);
        }
    }

    double expected = 0.0;
    for (std::size_t i = 1; i < window_mids.size(); ++i) {
        const double r = std::log(window_mids[i] / window_mids[i - 1]);
        expected += r * r;
    }
    expected = std::sqrt(expected);

    assert(rows.back().rolling_realized_vol.has_value());
    assert(almost_equal(*rows.back().rolling_realized_vol, expected, 1e-9));

    // repeated add/subtract must never drive the accumulator negative
    for (const lob::AnalyticsRow& row : rows) {
        if (row.rolling_realized_vol.has_value()) {
            assert(*row.rolling_realized_vol >= 0.0);
        }
    }
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
    assert(lhs.ofi_event == rhs.ofi_event);
    assert(lhs.rolling_ofi == rhs.rolling_ofi);
}

lob::BookSnapshot make_l1_snapshot(
    std::optional<lob::OrderBookLevel> bid,
    std::optional<lob::OrderBookLevel> ask) {
    lob::BookSnapshot snapshot;
    snapshot.best_bid = bid;
    snapshot.best_ask = ask;
    if (bid.has_value()) {
        snapshot.bids.push_back(*bid);
    }
    if (ask.has_value()) {
        snapshot.asks.push_back(*ask);
    }
    if (bid.has_value() && ask.has_value()) {
        snapshot.spread = ask->price - bid->price;
        snapshot.mid_price = (static_cast<double>(ask->price) + static_cast<double>(bid->price)) / 2.0;
    }
    return snapshot;
}

lob::LobsterMessage make_quote_message(double timestamp) {
    return lob::LobsterMessage{timestamp, lob::EventType::NewOrder, 1, 1, 1000, lob::Side::Buy};
}

void test_ofi_hand_computed_sequence() {
    lob::AnalyticsConfig config;
    config.trade_window_messages = 1000;
    lob::AnalyticsEngine engine(config);

    const auto bid = [](lob::Price px, lob::AggregateQuantity sz) {
        return lob::OrderBookLevel{px, sz, 1, lob::Side::Buy};
    };
    const auto ask = [](lob::Price px, lob::AggregateQuantity sz) {
        return lob::OrderBookLevel{px, sz, 1, lob::Side::Sell};
    };

    // first observed book is state, not flow
    lob::AnalyticsRow row = engine.on_message(
        make_quote_message(1.0), make_l1_snapshot(bid(10000, 10), ask(10100, 5)));
    assert(row.ofi_event == 0.0);
    assert(row.rolling_ofi == 0.0);

    // bid size grows at an unchanged touch: e = +15 - 10 = +5; ask unchanged: 0
    row = engine.on_message(
        make_quote_message(2.0), make_l1_snapshot(bid(10000, 15), ask(10100, 5)));
    assert(row.ofi_event == 5.0);
    assert(row.rolling_ofi == 5.0);

    // bid retreats 10000 -> 9900 (e -= 15); ask improves 10100 -> 10050 (e -= 4)
    row = engine.on_message(
        make_quote_message(3.0), make_l1_snapshot(bid(9900, 7), ask(10050, 4)));
    assert(row.ofi_event == -19.0);
    assert(row.rolling_ofi == -14.0);

    // bid side vanishes (e -= 7); ask unchanged: 0
    row = engine.on_message(
        make_quote_message(4.0), make_l1_snapshot(std::nullopt, ask(10050, 4)));
    assert(row.ofi_event == -7.0);
    assert(row.rolling_ofi == -21.0);
}

void test_rolling_ofi_evicts_beyond_window() {
    lob::AnalyticsConfig config;
    config.trade_window_messages = 2;
    lob::AnalyticsEngine engine(config);

    const auto bid = [](lob::Price px, lob::AggregateQuantity sz) {
        return lob::OrderBookLevel{px, sz, 1, lob::Side::Buy};
    };
    const auto ask = [](lob::Price px, lob::AggregateQuantity sz) {
        return lob::OrderBookLevel{px, sz, 1, lob::Side::Sell};
    };

    engine.on_message(make_quote_message(1.0), make_l1_snapshot(bid(10000, 10), ask(10100, 5)));  // e=0
    lob::AnalyticsRow row = engine.on_message(
        make_quote_message(2.0), make_l1_snapshot(bid(10000, 14), ask(10100, 5)));  // e=+4
    assert(row.rolling_ofi == 4.0);
    row = engine.on_message(
        make_quote_message(3.0), make_l1_snapshot(bid(10000, 20), ask(10100, 5)));  // e=+6
    // window of 2 holds {+4, +6}; the startup 0 was evicted
    assert(row.ofi_event == 6.0);
    assert(row.rolling_ofi == 10.0);
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

void test_prediction_config_defaults_and_round_trip() {
    lob::AnalyticsConfig config;
    assert(config.prediction_horizons_messages.empty());
    assert(config.prediction_horizons.empty());
    assert(config.prediction_report_out == std::nullopt);
    assert(config.prediction_report_out.empty());
    assert(!config.prediction_reporting_enabled());

    config.prediction_horizons_messages = {1, 3};
    config.prediction_report_out = "predictions.csv";
    assert(config.prediction_reporting_enabled());
    assert(config.prediction_report_out.has_value());
    assert(config.prediction_horizons_messages == std::vector<int>({1, 3}));
    assert(config.resolved_prediction_horizons_messages() == std::vector<int>({1, 3}));
    assert(config.prediction_report_out == "predictions.csv");

    lob::AnalyticsConfig legacy_config;
    legacy_config.prediction_horizons = {2, 4};
    legacy_config.prediction_report_out = "legacy.csv";
    assert(legacy_config.prediction_reporting_enabled());
    assert(legacy_config.resolved_prediction_horizons_messages() == std::vector<int>({2, 4}));

    lob::AnalyticsConfig precedence_config;
    precedence_config.prediction_horizons = {7, 9};
    precedence_config.prediction_horizons_messages = {1, 3};
    precedence_config.prediction_report_out = "precedence.csv";
    assert(precedence_config.prediction_reporting_enabled());
    assert(precedence_config.resolved_prediction_horizons_messages() == std::vector<int>({1, 3}));

    lob::AnalyticsConfig filtered_legacy_config;
    filtered_legacy_config.prediction_horizons = {
        0,
        2,
        static_cast<std::size_t>(std::numeric_limits<int>::max()) + 1u,
    };
    filtered_legacy_config.prediction_report_out = "filtered.csv";
    assert(filtered_legacy_config.prediction_reporting_enabled());
    assert(filtered_legacy_config.resolved_prediction_horizons_messages() == std::vector<int>({2}));

    lob::AnalyticsConfig invalid_config;
    invalid_config.prediction_horizons_messages = {0, -1};
    invalid_config.prediction_report_out = "invalid.csv";
    assert(!invalid_config.prediction_reporting_enabled());
    assert(invalid_config.resolved_prediction_horizons_messages().empty());

    std::vector<lob::LobsterMessage> messages = {
        {100.0, lob::EventType::NewOrder, 1, 50, 10000, lob::Side::Buy},
        {100.1, lob::EventType::NewOrder, 2, 60, 10100, lob::Side::Sell},
        {100.2, lob::EventType::NewOrder, 3, 40, 10050, lob::Side::Buy},
    };

    lob::MapOrderBook book;
    const std::vector<lob::AnalyticsRow> rows = lob::replay_with_analytics(messages, book, config);
    const std::vector<lob::PredictionSnapshot> snapshots = lob::collect_prediction_snapshots(rows);

    assert(rows.size() == messages.size());
    assert(snapshots.size() == rows.size());
    for (std::size_t index = 0; index < snapshots.size(); ++index) {
        assert(snapshots[index].message_index == index);
        assert(snapshots[index].mid_price == rows[index].mid_price);
        assert(snapshots[index].order_imbalance_top5 == rows[index].order_imbalance);
    }
}

void test_prediction_positive_label_when_first_non_zero_future_move_is_up() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, 0.8),
        make_prediction_snapshot(1, 100.0, -0.4),
        make_prediction_snapshot(2, 101.0, 0.2),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {2});
    assert(summaries.size() == 1);

    const lob::PredictionSummaryRow& summary = summaries.front();
    assert(summary.horizon_messages == 2);
    assert(summary.total_rows_seen == 3);
    assert(summary.eligible_rows_with_valid_mid == 3);
    assert(summary.labeled_rows == 2);
    assert(summary.skipped_no_valid_mid == 0);
    assert(summary.skipped_no_future_move_within_horizon == 1);
    assert(summary.skipped_zero_signal == 0);
    assert(summary.up_moves == 2);
    assert(summary.down_moves == 0);
    assert(summary.correct_predictions == 1);
    assert(summary.incorrect_predictions == 1);
    assert(almost_equal(summary.hit_rate, 0.5));
    assert(almost_equal(summary.information_coefficient, 0.0));
    assert(almost_equal(summary.coverage_vs_total, 2.0 / 3.0));
}

void test_prediction_negative_label_when_first_non_zero_future_move_is_down() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, -0.7),
        make_prediction_snapshot(1, 100.0, 0.6),
        make_prediction_snapshot(2, 99.0, -0.2),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {2});
    assert(summaries.size() == 1);

    const lob::PredictionSummaryRow& summary = summaries.front();
    assert(summary.horizon_messages == 2);
    assert(summary.total_rows_seen == 3);
    assert(summary.eligible_rows_with_valid_mid == 3);
    assert(summary.labeled_rows == 2);
    assert(summary.skipped_no_valid_mid == 0);
    assert(summary.skipped_no_future_move_within_horizon == 1);
    assert(summary.skipped_zero_signal == 0);
    assert(summary.up_moves == 0);
    assert(summary.down_moves == 2);
    assert(summary.correct_predictions == 1);
    assert(summary.incorrect_predictions == 1);
    assert(almost_equal(summary.hit_rate, 0.5));
    assert(almost_equal(summary.information_coefficient, 0.0));
    assert(almost_equal(summary.coverage_vs_total, 2.0 / 3.0));
}

void test_prediction_skips_invalid_current_mid() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, std::nullopt, 0.6),
        make_prediction_snapshot(1, 100.0, 0.5),
        make_prediction_snapshot(2, 101.0, 0.4),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {2});
    const lob::PredictionSummaryRow& summary = summaries.front();

    assert(summary.total_rows_seen == 3);
    assert(summary.eligible_rows_with_valid_mid == 2);
    assert(summary.labeled_rows == 1);
    assert(summary.skipped_no_valid_mid == 1);
    assert(summary.skipped_no_future_move_within_horizon == 1);
    assert(summary.skipped_zero_signal == 0);
    assert(summary.up_moves == 1);
    assert(summary.down_moves == 0);
    assert(summary.correct_predictions == 1);
    assert(summary.incorrect_predictions == 0);
    assert(almost_equal(summary.hit_rate, 1.0));
    assert(almost_equal(summary.information_coefficient, 0.0));
    assert(almost_equal(summary.coverage_vs_total, 1.0 / 3.0));
}

void test_prediction_skips_when_horizon_expires_with_only_zero_moves() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, 0.5),
        make_prediction_snapshot(1, 100.0, 0.3),
        make_prediction_snapshot(2, 100.0, -0.2),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {2});
    const lob::PredictionSummaryRow& summary = summaries.front();

    assert(summary.total_rows_seen == 3);
    assert(summary.eligible_rows_with_valid_mid == 3);
    assert(summary.labeled_rows == 0);
    assert(summary.skipped_no_valid_mid == 0);
    assert(summary.skipped_no_future_move_within_horizon == 3);
    assert(summary.skipped_zero_signal == 0);
    assert(summary.up_moves == 0);
    assert(summary.down_moves == 0);
    assert(summary.correct_predictions == 0);
    assert(summary.incorrect_predictions == 0);
    assert(almost_equal(summary.hit_rate, 0.0));
    assert(almost_equal(summary.information_coefficient, 0.0));
    assert(almost_equal(summary.coverage_vs_total, 0.0));
}

void test_prediction_zero_signal_is_skipped_instead_of_labeled() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, 0.0),
        make_prediction_snapshot(1, 101.0, 0.5),
        make_prediction_snapshot(2, 102.0, -0.5),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {1});
    const lob::PredictionSummaryRow& summary = summaries.front();

    assert(summary.total_rows_seen == 3);
    assert(summary.eligible_rows_with_valid_mid == 3);
    assert(summary.labeled_rows == 2);
    assert(summary.skipped_no_valid_mid == 0);
    assert(summary.skipped_no_future_move_within_horizon == 1);
    assert(summary.skipped_zero_signal == 1);
    assert(summary.up_moves == 2);
    assert(summary.down_moves == 0);
    assert(summary.correct_predictions == 1);
    assert(summary.incorrect_predictions == 0);
    assert(almost_equal(summary.hit_rate, 1.0));
    assert(almost_equal(summary.information_coefficient, 0.0));
    assert(almost_equal(summary.coverage_vs_total, 2.0 / 3.0));
}

void test_prediction_uses_first_non_zero_future_move_even_if_later_moves_reverse() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, 0.5),
        make_prediction_snapshot(1, 101.0, -0.4),
        make_prediction_snapshot(2, 99.0, 0.7),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {2});
    const lob::PredictionSummaryRow& summary = summaries.front();

    assert(summary.total_rows_seen == 3);
    assert(summary.eligible_rows_with_valid_mid == 3);
    assert(summary.labeled_rows == 2);
    assert(summary.skipped_no_valid_mid == 0);
    assert(summary.skipped_no_future_move_within_horizon == 1);
    assert(summary.skipped_zero_signal == 0);
    assert(summary.up_moves == 1);
    assert(summary.down_moves == 1);
    assert(summary.correct_predictions == 2);
    assert(summary.incorrect_predictions == 0);
    assert(almost_equal(summary.hit_rate, 1.0));
    assert(almost_equal(summary.information_coefficient, 1.0));
    assert(almost_equal(summary.coverage_vs_total, 2.0 / 3.0));
}

void test_prediction_multi_horizon_case_can_skip_shorter_horizon_and_label_longer_one() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, 0.5),
        make_prediction_snapshot(1, 100.0, 0.2),
        make_prediction_snapshot(2, 100.0, -0.3),
        make_prediction_snapshot(3, 101.0, 0.4),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {1, 3});
    assert(summaries.size() == 2);

    const lob::PredictionSummaryRow& short_horizon = summaries[0];
    assert(short_horizon.horizon_messages == 1);
    assert(short_horizon.total_rows_seen == 4);
    assert(short_horizon.eligible_rows_with_valid_mid == 4);
    assert(short_horizon.labeled_rows == 1);
    assert(short_horizon.skipped_no_valid_mid == 0);
    assert(short_horizon.skipped_no_future_move_within_horizon == 3);
    assert(short_horizon.skipped_zero_signal == 0);
    assert(short_horizon.up_moves == 1);
    assert(short_horizon.down_moves == 0);
    assert(short_horizon.correct_predictions == 0);
    assert(short_horizon.incorrect_predictions == 1);
    assert(almost_equal(short_horizon.hit_rate, 0.0));
    assert(almost_equal(short_horizon.information_coefficient, 0.0));
    assert(almost_equal(short_horizon.coverage_vs_total, 0.25));

    const lob::PredictionSummaryRow& long_horizon = summaries[1];
    assert(long_horizon.horizon_messages == 3);
    assert(long_horizon.total_rows_seen == 4);
    assert(long_horizon.eligible_rows_with_valid_mid == 4);
    assert(long_horizon.labeled_rows == 3);
    assert(long_horizon.skipped_no_valid_mid == 0);
    assert(long_horizon.skipped_no_future_move_within_horizon == 1);
    assert(long_horizon.skipped_zero_signal == 0);
    assert(long_horizon.up_moves == 3);
    assert(long_horizon.down_moves == 0);
    assert(long_horizon.correct_predictions == 2);
    assert(long_horizon.incorrect_predictions == 1);
    assert(almost_equal(long_horizon.hit_rate, 2.0 / 3.0));
    assert(almost_equal(long_horizon.information_coefficient, 0.0));
    assert(almost_equal(long_horizon.coverage_vs_total, 0.75));
}

void test_prediction_information_coefficient_uses_raw_imbalance_values() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, -1.0),
        make_prediction_snapshot(1, 99.0, -1.0),
        make_prediction_snapshot(2, 98.0, 1.0),
        make_prediction_snapshot(3, 99.0, 1.0),
        make_prediction_snapshot(4, 100.0, 0.1),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {1});
    const lob::PredictionSummaryRow& summary = summaries.front();

    assert(summary.labeled_rows == 4);
    assert(summary.up_moves == 2);
    assert(summary.down_moves == 2);
    assert(summary.correct_predictions == 4);
    assert(summary.information_coefficient > 0.999999);
}

void test_prediction_report_writer_emits_expected_header_and_rows() {
    const std::vector<lob::PredictionSnapshot> snapshots = {
        make_prediction_snapshot(0, 100.0, 0.5),
        make_prediction_snapshot(1, 101.0, 0.0),
        make_prediction_snapshot(2, 101.0, -0.5),
        make_prediction_snapshot(3, 100.0, 0.5),
        make_prediction_snapshot(4, std::nullopt, 0.2),
    };

    const std::vector<lob::PredictionSummaryRow> summaries =
        lob::summarize_prediction_horizons(snapshots, {1, 2});
    const auto output_path = make_temp_file("prediction_report");
    lob::write_prediction_report_csv(summaries, output_path.string());

    std::ifstream input(output_path);
    assert(input.is_open());

    std::string header;
    std::string first_row;
    std::string second_row;
    assert(std::getline(input, header));
    assert(std::getline(input, first_row));
    assert(std::getline(input, second_row));

    assert(
        header ==
        "horizon_messages,total_rows_seen,eligible_rows_with_valid_mid,labeled_rows,"
        "skipped_no_valid_mid,skipped_no_future_move_within_horizon,skipped_zero_signal,"
        "up_moves,down_moves,correct_predictions,incorrect_predictions,hit_rate,"
        "information_coefficient,coverage_vs_total");
    assert(first_row == "1,5,4,2,1,2,0,1,1,2,0,1.000000,1.000000,0.400000");
    assert(second_row == "2,5,4,3,1,1,1,1,2,2,0,1.000000,0.866025,0.600000");

    input.close();
    std::filesystem::remove(output_path);
}

}  // namespace

int main() {
    test_analytics_rows_cover_every_message();
    test_trade_metrics_and_realized_vol_are_populated();
    test_incremental_realized_vol_matches_recomputation();
    test_analytics_outputs_match_across_backends();
    test_ofi_hand_computed_sequence();
    test_rolling_ofi_evicts_beyond_window();
    test_prediction_config_defaults_and_round_trip();
    test_prediction_positive_label_when_first_non_zero_future_move_is_up();
    test_prediction_negative_label_when_first_non_zero_future_move_is_down();
    test_prediction_skips_invalid_current_mid();
    test_prediction_skips_when_horizon_expires_with_only_zero_moves();
    test_prediction_zero_signal_is_skipped_instead_of_labeled();
    test_prediction_uses_first_non_zero_future_move_even_if_later_moves_reverse();
    test_prediction_multi_horizon_case_can_skip_shorter_horizon_and_label_longer_one();
    test_prediction_information_coefficient_uses_raw_imbalance_values();
    test_prediction_report_writer_emits_expected_header_and_rows();
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
