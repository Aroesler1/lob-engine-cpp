#include <cassert>
#include <iostream>
#include <optional>
#include <vector>

#include "lob/order_book.hpp"
#include "lob/replay.hpp"
#include "lob/types.hpp"

namespace {

using lob::BookSnapshot;
using lob::EventType;
using lob::FlatVectorOrderBook;
using lob::LobsterMessage;
using lob::MapOrderBook;
using lob::OrderBook;
using lob::OrderBookLevel;
using lob::Price;
using lob::Side;

LobsterMessage make_message(
    double timestamp,
    EventType event_type,
    lob::OrderId order_id,
    lob::Quantity size,
    Price price,
    Side side) {
    return LobsterMessage{timestamp, event_type, order_id, size, price, side};
}

void assert_same_optional_level(
    const std::optional<OrderBookLevel>& lhs,
    const std::optional<OrderBookLevel>& rhs) {
    assert(lhs == rhs);
}

void assert_semantic_parity(
    const OrderBook& lhs,
    const OrderBook& rhs,
    const std::vector<Price>& bid_prices,
    const std::vector<Price>& ask_prices) {
    assert_same_optional_level(lhs.best_bid(), rhs.best_bid());
    assert_same_optional_level(lhs.best_ask(), rhs.best_ask());
    assert(lhs.active_order_count() == rhs.active_order_count());

    for (Price price : bid_prices) {
        assert_same_optional_level(lhs.level(Side::Buy, price), rhs.level(Side::Buy, price));
    }

    for (Price price : ask_prices) {
        assert_same_optional_level(lhs.level(Side::Sell, price), rhs.level(Side::Sell, price));
    }

    const BookSnapshot lhs_snapshot = lhs.snapshot(8);
    const BookSnapshot rhs_snapshot = rhs.snapshot(8);
    assert(lhs_snapshot == rhs_snapshot);
}

void test_map_and_flat_vector_match_after_each_event() {
    MapOrderBook map_book;
    FlatVectorOrderBook flat_book;

    const std::vector<LobsterMessage> messages = {
        make_message(0.001, EventType::NewOrder, 101, 100, 10000, Side::Buy),
        make_message(0.002, EventType::NewOrder, 102, 50, 10000, Side::Buy),
        make_message(0.003, EventType::NewOrder, 201, 70, 10200, Side::Sell),
        make_message(0.004, EventType::NewOrder, 202, 30, 10100, Side::Sell),
        make_message(0.005, EventType::PartialCancel, 102, 20, 10000, Side::Buy),
        make_message(0.006, EventType::ExecutionVisible, 202, 10, 10100, Side::Sell),
        make_message(0.007, EventType::NewOrder, 103, 40, 10100, Side::Buy),
        make_message(0.008, EventType::FullCancel, 101, 100, 10000, Side::Buy),
        make_message(0.009, EventType::ExecutionVisible, 202, 25, 10100, Side::Sell),
        make_message(0.010, EventType::NewOrder, 203, 25, 10300, Side::Sell),
        make_message(0.011, EventType::ExecutionVisible, 201, 70, 10200, Side::Sell),
        make_message(0.012, EventType::PartialCancel, 102, 30, 10000, Side::Buy),
        make_message(0.013, EventType::NewOrder, 104, 15, 9900, Side::Buy),
        make_message(0.014, EventType::ExecutionVisible, 103, 40, 10100, Side::Buy),
        make_message(0.015, EventType::ExecutionHidden, 999, 50, 10400, Side::Sell),
        make_message(0.016, EventType::CrossTrade, 998, 60, 10450, Side::Buy),
        make_message(0.017, EventType::TradingHalt, 997, 0, 0, Side::Sell),
    };

    const std::vector<Price> bid_prices = {10100, 10000, 9900};
    const std::vector<Price> ask_prices = {10100, 10200, 10300};

    for (const LobsterMessage& message : messages) {
        map_book.apply(message);
        flat_book.apply(message);
        assert_semantic_parity(map_book, flat_book, bid_prices, ask_prices);
    }

    const BookSnapshot final_snapshot = map_book.snapshot(8);
    assert(final_snapshot.best_bid == std::optional<OrderBookLevel>(OrderBookLevel{9900, 15, 1, Side::Buy}));
    assert(final_snapshot.best_ask == std::optional<OrderBookLevel>(OrderBookLevel{10300, 25, 1, Side::Sell}));
    assert(final_snapshot.spread == std::optional<Price>(400));
    assert(final_snapshot.active_order_count == 2);
    assert(final_snapshot.mid_price == std::optional<double>(10100.0));
}

void test_replay_helper_matches_direct_application() {
    const std::vector<LobsterMessage> messages = {
        make_message(0.100, EventType::NewOrder, 1, 10, 5000, Side::Buy),
        make_message(0.200, EventType::NewOrder, 2, 12, 5050, Side::Sell),
        make_message(0.300, EventType::PartialCancel, 1, 4, 5000, Side::Buy),
        make_message(0.400, EventType::ExecutionVisible, 2, 5, 5050, Side::Sell),
        make_message(0.500, EventType::FullCancel, 1, 6, 5000, Side::Buy),
    };

    MapOrderBook direct_book;
    for (const LobsterMessage& message : messages) {
        direct_book.apply(message);
    }

    MapOrderBook replayed_book;
    lob::replay_messages(messages, replayed_book);

    assert(direct_book.snapshot(4) == replayed_book.snapshot(4));
    assert(replayed_book.best_bid() == std::nullopt);
    assert(replayed_book.best_ask() == std::optional<OrderBookLevel>(OrderBookLevel{5050, 7, 1, Side::Sell}));
}

}  // namespace

int main() {
    test_map_and_flat_vector_match_after_each_event();
    test_replay_helper_matches_direct_application();

    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
