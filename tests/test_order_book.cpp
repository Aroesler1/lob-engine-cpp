#define CATCH_CONFIG_MAIN
#include <catch2/catch.hpp>

#include "lobster_parser.h"
#include "order_book.h"

#include <filesystem>

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

TEST_CASE("unknown order cancel is counted and does not crash", "[order_book]") {
    OrderBook book;

    book.cancelOrder(999, 5);

    REQUIRE(book.unknownIdCount() == 1);
    REQUIRE(book.orderCount() == 0);
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
