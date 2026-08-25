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


void test_seeded_levels_and_unknown_id_fallback() {
    lob::OrderBookBuildConfig config;
    config.seed_levels.push_back(lob::SeedLevel{lob::Side::Sell, 310800, 200});
    config.seed_levels.push_back(lob::SeedLevel{lob::Side::Buy, 309500, 300});
    lob::MapOrderBook book(config);

    // seeded liquidity is visible immediately
    assert(book.best_ask().has_value() && book.best_ask()->price == 310800);
    assert(book.best_ask()->total_size == 200);
    assert(book.best_bid().has_value() && book.best_bid()->total_size == 300);

    // an intraday order stacks on the seeded level
    book.apply(lob::LobsterMessage{1.0, lob::EventType::NewOrder, 42, 100, 310800, lob::Side::Sell});
    assert(book.level(lob::Side::Sell, 310800)->total_size == 300);

    // a cancel referencing an unknown (pre-open) id consumes seeded liquidity
    book.apply(lob::LobsterMessage{2.0, lob::EventType::FullCancel, 999, 200, 310800, lob::Side::Sell});
    assert(book.level(lob::Side::Sell, 310800)->total_size == 100);

    // further unknown reductions cannot touch tracked-order liquidity
    book.apply(lob::LobsterMessage{3.0, lob::EventType::FullCancel, 998, 500, 310800, lob::Side::Sell});
    assert(book.level(lob::Side::Sell, 310800)->total_size == 100);

    // the tracked order is still individually removable
    book.apply(lob::LobsterMessage{4.0, lob::EventType::FullCancel, 42, 100, 310800, lob::Side::Sell});
    assert(!book.level(lob::Side::Sell, 310800).has_value());

    // unknown execution fully drains a purely seeded level and erases it
    book.apply(lob::LobsterMessage{5.0, lob::EventType::ExecutionVisible, 997, 300, 309500, lob::Side::Buy});
    assert(!book.best_bid().has_value());
}

void test_seeded_backends_stay_in_parity() {
    lob::OrderBookBuildConfig config;
    config.seed_levels.push_back(lob::SeedLevel{lob::Side::Sell, 101000, 400});
    config.seed_levels.push_back(lob::SeedLevel{lob::Side::Buy, 100000, 250});

    lob::MapOrderBook map_book(config);
    lob::FlatVectorOrderBook flat_book(config);

    const std::vector<lob::LobsterMessage> messages = {
        {1.0, lob::EventType::NewOrder, 1, 100, 100500, lob::Side::Buy},
        {2.0, lob::EventType::PartialCancel, 777, 150, 101000, lob::Side::Sell},  // unknown id
        {3.0, lob::EventType::NewOrder, 2, 50, 101000, lob::Side::Sell},
        {4.0, lob::EventType::ExecutionVisible, 778, 250, 100000, lob::Side::Buy},  // unknown id
        {5.0, lob::EventType::FullCancel, 1, 100, 100500, lob::Side::Buy},
    };
    for (const lob::LobsterMessage& message : messages) {
        map_book.apply(message);
        flat_book.apply(message);
        assert(map_book.snapshot(10) == flat_book.snapshot(10));
    }
    assert(map_book.level(lob::Side::Sell, 101000)->total_size == 300);
}

}  // namespace

int main() {
    test_seeded_levels_and_unknown_id_fallback();
    test_seeded_backends_stay_in_parity();
    test_map_and_flat_vector_match_after_each_event();
    test_replay_helper_matches_direct_application();

    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
