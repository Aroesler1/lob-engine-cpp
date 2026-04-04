#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <unordered_map>

struct LobsterMessage;

struct PriceLevel {
    int64_t total_size = 0;
    int32_t order_count = 0;
};

struct Order {
    int64_t order_id = 0;
    int64_t price = 0;
    int64_t remaining = 0;
    int side = 0;
};

class OrderBook {
public:
    void processMessage(const LobsterMessage& msg);
    void addOrder(int64_t order_id, int side, int64_t price, int64_t size);
    void cancelOrder(int64_t order_id, int64_t size);
    void executeOrder(int64_t order_id, int64_t size);

    std::optional<int64_t> bestBid() const;
    std::optional<int64_t> bestAsk() const;
    std::optional<PriceLevel> nthLevel(int side, int n) const;
    const std::map<int64_t, PriceLevel, std::greater<int64_t>>& bids() const;
    const std::map<int64_t, PriceLevel>& asks() const;
    std::optional<Order> getOrder(int64_t order_id) const;
    std::size_t orderCount() const;
    std::size_t hiddenExecutionCount() const;
    std::size_t crossTradeCount() const;
    std::size_t unknownIdCount() const;
    bool halted() const;

private:
    void applyReduction(int64_t order_id, int64_t size, const char* action);

    std::unordered_map<int64_t, Order> orders_;
    std::map<int64_t, PriceLevel, std::greater<int64_t>> bids_;
    std::map<int64_t, PriceLevel> asks_;
    std::size_t hidden_exec_count_ = 0;
    std::size_t cross_trade_count_ = 0;
    std::size_t unknown_id_count_ = 0;
    bool halted_ = false;
};
