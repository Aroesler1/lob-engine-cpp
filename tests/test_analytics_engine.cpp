#define CATCH_CONFIG_MAIN
#include <catch2/catch.hpp>

#include "analytics_engine.h"
#include "lobster_parser.h"
#include "order_book.h"

#include <cmath>

namespace {

void requireRecordInvariants(const AnalyticsRecord& record) {
    REQUIRE(record.bid_depth_1 >= 0);
    REQUIRE(record.ask_depth_1 >= 0);
    REQUIRE(record.bid_depth_1 <= record.bid_depth_5);
    REQUIRE(record.bid_depth_5 <= record.bid_depth_10);
    REQUIRE(record.ask_depth_1 <= record.ask_depth_5);
    REQUIRE(record.ask_depth_5 <= record.ask_depth_10);
    REQUIRE(record.order_imbalance >= Approx(-1.0));
    REQUIRE(record.order_imbalance <= Approx(1.0));
    REQUIRE(record.trade_flow_imbalance >= Approx(-1.0));
    REQUIRE(record.trade_flow_imbalance <= Approx(1.0));

    if (!std::isnan(record.best_bid) && !std::isnan(record.best_ask)) {
        REQUIRE(record.best_bid <= record.best_ask);
        REQUIRE(record.spread == Approx(record.best_ask - record.best_bid));
        REQUIRE(record.mid == Approx((record.best_bid + record.best_ask) / 2.0));
    }

    if (!std::isnan(record.rolling_realized_vol)) {
        REQUIRE(record.rolling_realized_vol >= 0.0);
    }
}

}  // namespace

TEST_CASE("analytics engine emits cumulative depths and NaN price fields when the book is one-sided", "[analytics]") {
    AnalyticsEngine engine(AnalyticsConfig{2, 1.0});
    OrderBook book;

    const LobsterMessage add_bid{1.0, 1, 101, 10, 1000000, 1};
    book.processMessage(add_bid);
    const AnalyticsRecord record = engine.onMessage(add_bid, book);

    REQUIRE(record.timestamp == Approx(1.0));
    REQUIRE(record.best_bid == Approx(100.0));
    REQUIRE(std::isnan(record.best_ask));
    REQUIRE(std::isnan(record.spread));
    REQUIRE(std::isnan(record.mid));
    REQUIRE(record.bid_depth_1 == 10);
    REQUIRE(record.bid_depth_5 == 10);
    REQUIRE(record.bid_depth_10 == 10);
    REQUIRE(record.ask_depth_1 == 0);
    REQUIRE(record.ask_depth_5 == 0);
    REQUIRE(record.ask_depth_10 == 0);
    REQUIRE(record.order_imbalance == Approx(1.0));
    REQUIRE(std::isnan(record.rolling_vwap));
    REQUIRE(record.trade_flow_imbalance == Approx(0.0));
    REQUIRE(std::isnan(record.rolling_realized_vol));
}

TEST_CASE("analytics engine rolls trade and volatility windows forward", "[analytics]") {
    AnalyticsEngine engine(AnalyticsConfig{1, 1.0});
    OrderBook book;

    const LobsterMessage add_bid{1.0, 1, 101, 10, 1000000, 1};
    const LobsterMessage add_ask_1{1.1, 1, 201, 10, 1010000, -1};
    const LobsterMessage add_ask_2{1.2, 1, 202, 10, 1020000, -1};
    const LobsterMessage improve_bid{1.3, 1, 102, 5, 1005000, 1};
    const LobsterMessage execute_ask_1{1.4, 4, 201, 10, 1010000, -1};
    const LobsterMessage execute_ask_2{1.5, 4, 202, 4, 1020000, -1};
    const LobsterMessage stale_update{2.7, 1, 203, 5, 1030000, -1};

    book.processMessage(add_bid);
    engine.onMessage(add_bid, book);

    book.processMessage(add_ask_1);
    engine.onMessage(add_ask_1, book);

    book.processMessage(add_ask_2);
    engine.onMessage(add_ask_2, book);

    book.processMessage(improve_bid);
    const AnalyticsRecord improved_record = engine.onMessage(improve_bid, book);
    REQUIRE(improved_record.mid == Approx(100.75));
    REQUIRE(improved_record.rolling_realized_vol > 0.0);

    book.processMessage(execute_ask_1);
    const AnalyticsRecord first_trade_record = engine.onMessage(execute_ask_1, book);
    REQUIRE(first_trade_record.rolling_vwap == Approx(101.0));
    REQUIRE(first_trade_record.trade_flow_imbalance == Approx(-1.0));

    book.processMessage(execute_ask_2);
    const AnalyticsRecord second_trade_record = engine.onMessage(execute_ask_2, book);
    REQUIRE(second_trade_record.rolling_vwap == Approx(102.0));
    REQUIRE(second_trade_record.trade_flow_imbalance == Approx(-1.0));

    book.processMessage(stale_update);
    const AnalyticsRecord stale_record = engine.onMessage(stale_update, book);
    REQUIRE(stale_record.best_ask == Approx(102.0));
    REQUIRE(std::isnan(stale_record.rolling_realized_vol));
}

TEST_CASE("trade metrics only change on trade events and the rolling window drops the oldest trade", "[analytics]") {
    AnalyticsEngine engine(AnalyticsConfig{2, 10.0});
    OrderBook book;

    const LobsterMessage add_bid{1.0, 1, 101, 10, 1000000, 1};
    const LobsterMessage add_ask_1{1.1, 1, 201, 10, 1002000, -1};
    const LobsterMessage buy_trade{1.2, 4, 101, 4, 1000000, 1};
    const LobsterMessage add_ask_2{1.3, 1, 202, 5, 1003000, -1};
    const LobsterMessage sell_trade_1{1.4, 4, 201, 5, 1002000, -1};
    const LobsterMessage add_ask_3{1.5, 1, 203, 6, 1004000, -1};
    const LobsterMessage sell_trade_2{1.6, 4, 202, 5, 1003000, -1};

    for (const auto& message : {add_bid, add_ask_1}) {
        book.processMessage(message);
        requireRecordInvariants(engine.onMessage(message, book));
    }

    book.processMessage(buy_trade);
    const AnalyticsRecord buy_trade_record = engine.onMessage(buy_trade, book);
    requireRecordInvariants(buy_trade_record);
    REQUIRE(buy_trade_record.rolling_vwap == Approx(100.0));
    REQUIRE(buy_trade_record.trade_flow_imbalance == Approx(1.0));

    book.processMessage(add_ask_2);
    const AnalyticsRecord non_trade_record = engine.onMessage(add_ask_2, book);
    requireRecordInvariants(non_trade_record);
    REQUIRE(non_trade_record.rolling_vwap == Approx(buy_trade_record.rolling_vwap));
    REQUIRE(non_trade_record.trade_flow_imbalance == Approx(buy_trade_record.trade_flow_imbalance));

    book.processMessage(sell_trade_1);
    const AnalyticsRecord mixed_trade_record = engine.onMessage(sell_trade_1, book);
    requireRecordInvariants(mixed_trade_record);
    REQUIRE(mixed_trade_record.rolling_vwap == Approx((100.0 * 4.0 + 100.2 * 5.0) / 9.0));
    REQUIRE(mixed_trade_record.trade_flow_imbalance == Approx(-1.0 / 9.0));

    book.processMessage(add_ask_3);
    const AnalyticsRecord later_non_trade_record = engine.onMessage(add_ask_3, book);
    requireRecordInvariants(later_non_trade_record);
    REQUIRE(later_non_trade_record.rolling_vwap == Approx(mixed_trade_record.rolling_vwap));
    REQUIRE(later_non_trade_record.trade_flow_imbalance == Approx(mixed_trade_record.trade_flow_imbalance));

    book.processMessage(sell_trade_2);
    const AnalyticsRecord rolled_trade_record = engine.onMessage(sell_trade_2, book);
    requireRecordInvariants(rolled_trade_record);
    REQUIRE(rolled_trade_record.rolling_vwap == Approx((100.2 * 5.0 + 100.3 * 5.0) / 10.0));
    REQUIRE(rolled_trade_record.trade_flow_imbalance == Approx(-1.0));
}
