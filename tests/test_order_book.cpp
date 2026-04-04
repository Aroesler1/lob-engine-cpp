#define CATCH_CONFIG_MAIN
#include <catch2/catch.hpp>

#include "lobster_parser.h"
#include "order_book.h"

#include <filesystem>

namespace {

void requireLevel(const OrderBook& book, int side, int index, int64_t expected_size, int32_t expected_orders) {
    const auto level = book.nthLevel(side, index);
    REQUIRE(level.has_value());
    REQUIRE(level->total_size == expected_size);
    REQUIRE(level->order_count == expected_orders);
}

}  // namespace

TEST_CASE("add single buy order populates best bid", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1000000, 25);

    REQUIRE(book.bestBid().has_value());
    REQUIRE(*book.bestBid() == 1000000);
    REQUIRE_FALSE(book.bestAsk().has_value());
    REQUIRE(book.orderCount() == 1);

    const auto level = book.nthLevel(1, 0);
    REQUIRE(level.has_value());
    REQUIRE(level->total_size == 25);
    REQUIRE(level->order_count == 1);
}

TEST_CASE("add buy and sell orders produces a non-negative spread", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1000000, 25);
    book.addOrder(201, -1, 1002000, 30);

    REQUIRE(book.bestBid() == 1000000);
    REQUIRE(book.bestAsk() == 1002000);
    REQUIRE(*book.bestAsk() - *book.bestBid() == 2000);
}

TEST_CASE("partial cancel reduces remaining size without removing the order", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1000000, 25);
    book.cancelOrder(101, 10);

    const auto order = book.getOrder(101);
    REQUIRE(order.has_value());
    REQUIRE(order->remaining == 15);
    REQUIRE(book.orderCount() == 1);

    const auto level = book.nthLevel(1, 0);
    REQUIRE(level.has_value());
    REQUIRE(level->total_size == 15);
    REQUIRE(level->order_count == 1);
}

TEST_CASE("full cancel removes the order and erases the final level", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1000000, 25);
    book.cancelOrder(101, 25);

    REQUIRE(book.orderCount() == 0);
    REQUIRE_FALSE(book.getOrder(101).has_value());
    REQUIRE_FALSE(book.nthLevel(1, 0).has_value());
    REQUIRE_FALSE(book.bestBid().has_value());
}

TEST_CASE("execute updates book state with the same semantics as cancel", "[order_book]") {
    OrderBook book;

    book.addOrder(201, -1, 1002000, 40);
    book.executeOrder(201, 18);

    const auto order = book.getOrder(201);
    REQUIRE(order.has_value());
    REQUIRE(order->remaining == 22);

    const auto level = book.nthLevel(-1, 0);
    REQUIRE(level.has_value());
    REQUIRE(level->total_size == 22);
    REQUIRE(level->order_count == 1);
}

TEST_CASE("multiple orders at the same price aggregate size and count", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1000000, 25);
    book.addOrder(102, 1, 1000000, 15);

    const auto level = book.nthLevel(1, 0);
    REQUIRE(level.has_value());
    REQUIRE(level->total_size == 40);
    REQUIRE(level->order_count == 2);
}

TEST_CASE("bid-side mutations update aggregated levels and move the best bid when a level empties", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1000000, 10);
    book.addOrder(102, 1, 1000000, 5);
    book.addOrder(103, 1, 999500, 7);

    REQUIRE(book.bestBid() == 1000000);
    requireLevel(book, 1, 0, 15, 2);
    requireLevel(book, 1, 1, 7, 1);

    book.cancelOrder(101, 4);
    REQUIRE(book.getOrder(101)->remaining == 6);
    requireLevel(book, 1, 0, 11, 2);

    book.executeOrder(102, 5);
    REQUIRE_FALSE(book.getOrder(102).has_value());
    REQUIRE(book.bestBid() == 1000000);
    requireLevel(book, 1, 0, 6, 1);

    book.cancelOrder(101, 6);
    REQUIRE_FALSE(book.getOrder(101).has_value());
    REQUIRE(book.bestBid() == 999500);
    requireLevel(book, 1, 0, 7, 1);
    REQUIRE_FALSE(book.nthLevel(1, 1).has_value());
}

TEST_CASE("ask-side mutations aggregate same-price orders and clamp over-cancels to the resting quantity", "[order_book]") {
    OrderBook book;

    book.addOrder(201, -1, 1002000, 8);
    book.addOrder(202, -1, 1002000, 5);
    book.addOrder(203, -1, 1004000, 3);

    REQUIRE(book.bestAsk() == 1002000);
    requireLevel(book, -1, 0, 13, 2);
    requireLevel(book, -1, 1, 3, 1);

    book.executeOrder(201, 3);
    REQUIRE(book.getOrder(201)->remaining == 5);
    requireLevel(book, -1, 0, 10, 2);

    // Current engine semantics clamp reductions to the order's remaining quantity.
    book.cancelOrder(202, 50);
    REQUIRE_FALSE(book.getOrder(202).has_value());
    REQUIRE(book.bestAsk() == 1002000);
    requireLevel(book, -1, 0, 5, 1);

    book.executeOrder(201, 5);
    REQUIRE_FALSE(book.getOrder(201).has_value());
    REQUIRE(book.bestAsk() == 1004000);
    requireLevel(book, -1, 0, 3, 1);
    REQUIRE_FALSE(book.nthLevel(-1, 1).has_value());
}

TEST_CASE("missing cancel and execute leave the book unchanged while incrementing the unknown id counter", "[order_book]") {
    OrderBook book;
    book.addOrder(101, 1, 1000000, 25);

    book.cancelOrder(999, 5);
    book.executeOrder(999, 7);

    REQUIRE(book.unknownIdCount() == 2);
    REQUIRE(book.orderCount() == 1);
    REQUIRE(book.bestBid() == 1000000);
    requireLevel(book, 1, 0, 25, 1);
}

TEST_CASE("hidden execution increments its counter without mutating the book", "[order_book]") {
    OrderBook book;
    book.addOrder(101, 1, 1000000, 25);

    const auto before = book.nthLevel(1, 0);
    REQUIRE(before.has_value());

    const LobsterMessage hidden_exec{34200.0, 5, 999, 100, 0, 0};
    book.processMessage(hidden_exec);

    const auto after = book.nthLevel(1, 0);
    REQUIRE(after.has_value());
    REQUIRE(after->total_size == before->total_size);
    REQUIRE(after->order_count == before->order_count);
    REQUIRE(book.hiddenExecutionCount() == 1);
}

TEST_CASE("nthLevel returns successive bid levels in sorted order", "[order_book]") {
    OrderBook book;

    book.addOrder(101, 1, 1003000, 10);
    book.addOrder(102, 1, 1002000, 20);
    book.addOrder(103, 1, 1001000, 30);

    REQUIRE(book.nthLevel(1, 0)->total_size == 10);
    REQUIRE(book.nthLevel(1, 1)->total_size == 20);
    REQUIRE(book.nthLevel(1, 2)->total_size == 30);
    REQUIRE_FALSE(book.nthLevel(1, 3).has_value());
}

TEST_CASE("replay sample file leaves a valid quoted market", "[order_book]") {
    const auto sample_path = std::filesystem::path(TEST_DATA_DIR) / "sample_messages.csv";
    const auto messages = parseLobsterCsvFile(sample_path);

    REQUIRE(messages.size() >= 20);

    OrderBook book;
    for (const auto& message : messages) {
        book.processMessage(message);
    }

    REQUIRE(book.bestBid().has_value());
    REQUIRE(book.bestAsk().has_value());
    REQUIRE(*book.bestAsk() - *book.bestBid() >= 0);
}
