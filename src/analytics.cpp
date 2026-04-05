#include "lob/analytics.hpp"

#include <algorithm>
#include <cstddef>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace lob {
namespace {

AggregateQuantity depth_total(const std::vector<OrderBookLevel>& levels, std::size_t depth) {
    AggregateQuantity total = 0;
    const std::size_t count = std::min(depth, levels.size());
    for (std::size_t index = 0; index < count; ++index) {
        total += levels[index].total_size;
    }
    return total;
}

bool is_trade_event(EventType event_type) noexcept {
    return event_type == EventType::ExecutionVisible ||
           event_type == EventType::ExecutionHidden ||
           event_type == EventType::CrossTrade;
}

template <typename T>
void write_optional(std::ofstream& output, const std::optional<T>& value) {
    if (value.has_value()) {
        output << *value;
    }
}

template <typename T>
class SlidingWindowBuffer {
public:
    explicit SlidingWindowBuffer(std::size_t reserve_hint = 0) {
        if (reserve_hint > 0) {
            values_.reserve(reserve_hint);
        }
    }

    void clear() {
        values_.clear();
        start_index_ = 0;
    }

    template <typename... Args>
    void emplace_back(Args&&... args) {
        values_.emplace_back(std::forward<Args>(args)...);
    }

    void pop_front() {
        if (start_index_ >= values_.size()) {
            return;
        }
        ++start_index_;
        maybe_compact();
    }

    bool empty() const noexcept {
        return size() == 0;
    }

    std::size_t size() const noexcept {
        return values_.size() - start_index_;
    }

    const T& front() const {
        return values_[start_index_];
    }

    const T& operator[](std::size_t index) const {
        return values_[start_index_ + index];
    }

private:
    void maybe_compact() {
        if (start_index_ == 0 || start_index_ < 1024 || start_index_ * 2 < values_.size()) {
            return;
        }

        values_.erase(values_.begin(), values_.begin() + static_cast<std::ptrdiff_t>(start_index_));
        start_index_ = 0;
    }

    std::vector<T> values_;
    std::size_t start_index_{0};
};

}  // namespace

struct AnalyticsEngine::Impl {
    struct TradeContribution {
        double notional{0.0};
        double quantity{0.0};
        double signed_quantity{0.0};
    };

    struct MidSample {
        double timestamp{0.0};
        double mid_price{0.0};
    };

    explicit Impl(const AnalyticsConfig& config)
        : trade_window_capacity(std::max<std::size_t>(1, config.trade_window_messages)),
          trade_window(trade_window_capacity),
          mid_window(config.expected_messages) {}

    bool push_trade(const TradeContribution& contribution, TradeContribution& evicted) {
        if (trade_window_size < trade_window_capacity) {
            trade_window[(trade_window_head + trade_window_size) % trade_window_capacity] = contribution;
            ++trade_window_size;
            return false;
        }

        evicted = trade_window[trade_window_head];
        trade_window[trade_window_head] = contribution;
        trade_window_head = (trade_window_head + 1) % trade_window_capacity;
        return true;
    }

    void clear() {
        trade_window_size = 0;
        trade_window_head = 0;
        mid_window.clear();
        rolling_notional = 0.0;
        rolling_quantity = 0.0;
        rolling_signed_quantity = 0.0;
    }

    std::size_t trade_window_capacity{1};
    std::vector<TradeContribution> trade_window;
    std::size_t trade_window_head{0};
    std::size_t trade_window_size{0};
    SlidingWindowBuffer<MidSample> mid_window;
    double rolling_notional{0.0};
    double rolling_quantity{0.0};
    double rolling_signed_quantity{0.0};
};

AnalyticsEngine::AnalyticsEngine(AnalyticsConfig config)
    : config_(config), impl_(std::make_unique<Impl>(config_)) {}

AnalyticsEngine::~AnalyticsEngine() = default;
AnalyticsEngine::AnalyticsEngine(AnalyticsEngine&&) noexcept = default;
AnalyticsEngine& AnalyticsEngine::operator=(AnalyticsEngine&&) noexcept = default;

void AnalyticsEngine::reset() {
    impl_->clear();
}

AnalyticsRow AnalyticsEngine::on_message(const LobsterMessage& message, const BookSnapshot& snapshot) {
    AnalyticsRow row;
    on_message(message, snapshot, row);
    return row;
}

void AnalyticsEngine::on_message(
    const LobsterMessage& message,
    const BookSnapshot& snapshot,
    AnalyticsRow& row) {
    row = AnalyticsRow{};
    row.timestamp = message.timestamp;
    row.best_bid = snapshot.best_bid.has_value() ? std::optional<Price>(snapshot.best_bid->price) : std::nullopt;
    row.best_ask = snapshot.best_ask.has_value() ? std::optional<Price>(snapshot.best_ask->price) : std::nullopt;
    row.spread = snapshot.spread;
    row.mid_price = snapshot.mid_price;
    row.bid_depth_1 = depth_total(snapshot.bids, 1);
    row.bid_depth_5 = depth_total(snapshot.bids, 5);
    row.bid_depth_10 = depth_total(snapshot.bids, 10);
    row.ask_depth_1 = depth_total(snapshot.asks, 1);
    row.ask_depth_5 = depth_total(snapshot.asks, 5);
    row.ask_depth_10 = depth_total(snapshot.asks, 10);

    const double depth_balance = static_cast<double>(row.bid_depth_5 + row.ask_depth_5);
    if (depth_balance > 0.0) {
        row.order_imbalance = static_cast<double>(row.bid_depth_5 - row.ask_depth_5) / depth_balance;
    }

    Impl::TradeContribution contribution{};
    if (is_trade_event(message.event_type) && message.size > 0 && message.price > 0) {
        contribution.quantity = static_cast<double>(message.size);
        contribution.notional = static_cast<double>(message.price) * contribution.quantity;
        contribution.signed_quantity = contribution.quantity * (message.direction == Side::Buy ? 1.0 : -1.0);
    }
    Impl::TradeContribution evicted{};
    const bool replaced_oldest = impl_->push_trade(contribution, evicted);
    impl_->rolling_notional += contribution.notional;
    impl_->rolling_quantity += contribution.quantity;
    impl_->rolling_signed_quantity += contribution.signed_quantity;
    if (replaced_oldest) {
        impl_->rolling_notional -= evicted.notional;
        impl_->rolling_quantity -= evicted.quantity;
        impl_->rolling_signed_quantity -= evicted.signed_quantity;
    }
    if (impl_->rolling_quantity > 0.0) {
        row.rolling_vwap = impl_->rolling_notional / impl_->rolling_quantity;
        row.trade_flow_imbalance = impl_->rolling_signed_quantity / impl_->rolling_quantity;
    }

    if (snapshot.mid_price.has_value() && *snapshot.mid_price > 0.0) {
        impl_->mid_window.emplace_back(Impl::MidSample{message.timestamp, *snapshot.mid_price});
    }
    while (!impl_->mid_window.empty() &&
           impl_->mid_window.front().timestamp < (message.timestamp - config_.realized_vol_window_seconds)) {
        impl_->mid_window.pop_front();
    }
    if (impl_->mid_window.size() >= 2) {
        double realized = 0.0;
        for (std::size_t index = 1; index < impl_->mid_window.size(); ++index) {
            const double previous = impl_->mid_window[index - 1].mid_price;
            const double current = impl_->mid_window[index].mid_price;
            if (previous > 0.0 && current > 0.0) {
                const double log_return = std::log(current / previous);
                realized += log_return * log_return;
            }
        }
        row.rolling_realized_vol = std::sqrt(realized);
    }

}

std::vector<AnalyticsRow> replay_with_analytics(
    const std::vector<LobsterMessage>& messages,
    OrderBook& book,
    AnalyticsConfig config) {
    if (config.expected_messages == 0) {
        config.expected_messages = messages.size();
    }

    AnalyticsEngine engine(config);
    std::vector<AnalyticsRow> rows;
    rows.reserve(messages.size());
    const std::size_t snapshot_depth = std::max<std::size_t>(config.depth_levels, 10);
    for (const LobsterMessage& message : messages) {
        book.apply(message);
        const BookSnapshot snapshot = book.snapshot(snapshot_depth);
        rows.emplace_back();
        engine.on_message(message, snapshot, rows.back());
    }
    return rows;
}

void write_analytics_csv(const std::vector<AnalyticsRow>& rows, const std::string& output_path) {
    std::ofstream output(output_path);
    if (!output.is_open()) {
        throw std::runtime_error("Could not open analytics CSV output path");
    }

    output << "timestamp,best_bid,best_ask,spread,mid,bid_depth_1,bid_depth_5,bid_depth_10,"
              "ask_depth_1,ask_depth_5,ask_depth_10,order_imbalance,rolling_vwap,trade_flow_imbalance,"
              "rolling_realized_vol\n";
    output << std::fixed << std::setprecision(6);
    for (const AnalyticsRow& row : rows) {
        output << row.timestamp << ',';
        write_optional(output, row.best_bid);
        output << ',';
        write_optional(output, row.best_ask);
        output << ',';
        write_optional(output, row.spread);
        output << ',';
        write_optional(output, row.mid_price);
        output << ',';
        output << row.bid_depth_1 << ','
               << row.bid_depth_5 << ','
               << row.bid_depth_10 << ','
               << row.ask_depth_1 << ','
               << row.ask_depth_5 << ','
               << row.ask_depth_10 << ',';
        write_optional(output, row.order_imbalance);
        output << ',';
        write_optional(output, row.rolling_vwap);
        output << ',';
        write_optional(output, row.trade_flow_imbalance);
        output << ',';
        write_optional(output, row.rolling_realized_vol);
        output << '\n';
    }
}

}  // namespace lob
