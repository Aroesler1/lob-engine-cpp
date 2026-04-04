#pragma once

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "lob/types.hpp"

namespace lob {

enum class OrderBookBackend {
    Map,
    FlatVector,
};

struct BookSnapshot {
    std::optional<OrderBookLevel> best_bid;
    std::optional<OrderBookLevel> best_ask;
    std::vector<OrderBookLevel> bids;
    std::vector<OrderBookLevel> asks;
    std::size_t active_order_count{0};
    std::optional<Price> spread;
    std::optional<double> mid_price;
};

bool operator==(const BookSnapshot& lhs, const BookSnapshot& rhs) noexcept;

class OrderBook {
public:
    virtual ~OrderBook() = default;

    virtual void apply(const LobsterMessage& message) = 0;

    virtual std::optional<OrderBookLevel> best_bid() const = 0;
    virtual std::optional<OrderBookLevel> best_ask() const = 0;
    virtual std::optional<OrderBookLevel> level(Side side, Price price) const = 0;
    virtual std::vector<OrderBookLevel> levels(Side side, std::size_t depth) const = 0;
    virtual BookSnapshot snapshot(std::size_t depth) const = 0;
    virtual std::size_t active_order_count() const noexcept = 0;
};

class MapOrderBook final : public OrderBook {
public:
    MapOrderBook();
    ~MapOrderBook() override;

    MapOrderBook(MapOrderBook&&) noexcept;
    MapOrderBook& operator=(MapOrderBook&&) noexcept;

    MapOrderBook(const MapOrderBook&) = delete;
    MapOrderBook& operator=(const MapOrderBook&) = delete;

    void apply(const LobsterMessage& message) override;

    std::optional<OrderBookLevel> best_bid() const override;
    std::optional<OrderBookLevel> best_ask() const override;
    std::optional<OrderBookLevel> level(Side side, Price price) const override;
    std::vector<OrderBookLevel> levels(Side side, std::size_t depth) const override;
    BookSnapshot snapshot(std::size_t depth) const override;
    std::size_t active_order_count() const noexcept override;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

class FlatVectorOrderBook final : public OrderBook {
public:
    FlatVectorOrderBook();
    ~FlatVectorOrderBook() override;

    FlatVectorOrderBook(FlatVectorOrderBook&&) noexcept;
    FlatVectorOrderBook& operator=(FlatVectorOrderBook&&) noexcept;

    FlatVectorOrderBook(const FlatVectorOrderBook&) = delete;
    FlatVectorOrderBook& operator=(const FlatVectorOrderBook&) = delete;

    void apply(const LobsterMessage& message) override;

    std::optional<OrderBookLevel> best_bid() const override;
    std::optional<OrderBookLevel> best_ask() const override;
    std::optional<OrderBookLevel> level(Side side, Price price) const override;
    std::vector<OrderBookLevel> levels(Side side, std::size_t depth) const override;
    BookSnapshot snapshot(std::size_t depth) const override;
    std::size_t active_order_count() const noexcept override;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

std::unique_ptr<OrderBook> make_order_book(OrderBookBackend backend);

const char* to_string(OrderBookBackend backend) noexcept;
std::optional<OrderBookBackend> parse_order_book_backend(const std::string& value);

}  // namespace lob
