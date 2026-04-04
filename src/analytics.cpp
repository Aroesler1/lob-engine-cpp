#include "lob/analytics.hpp"

#include <cmath>
#include <deque>
#include <fstream>
#include <iomanip>
#include <limits>
#include <stdexcept>

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

    std::deque<TradeContribution> trade_window;
    std::deque<MidSample> mid_window;
    double rolling_notional{0.0};
    double rolling_quantity{0.0};
    double rolling_signed_quantity{0.0};
};

AnalyticsEngine::AnalyticsEngine(AnalyticsConfig config)
    : config_(config), impl_(std::make_unique<Impl>()) {}

AnalyticsEngine::~AnalyticsEngine() = default;
AnalyticsEngine::AnalyticsEngine(AnalyticsEngine&&) noexcept = default;
AnalyticsEngine& AnalyticsEngine::operator=(AnalyticsEngine&&) noexcept = default;

void AnalyticsEngine::reset() {
    impl_ = std::make_unique<Impl>();
}

AnalyticsRow AnalyticsEngine::on_message(const LobsterMessage& message, const BookSnapshot& snapshot) {
    AnalyticsRow row;
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
    impl_->trade_window.push_back(contribution);
    impl_->rolling_notional += contribution.notional;
    impl_->rolling_quantity += contribution.quantity;
    impl_->rolling_signed_quantity += contribution.signed_quantity;
    while (impl_->trade_window.size() > std::max<std::size_t>(1, config_.trade_window_messages)) {
        const Impl::TradeContribution& evicted = impl_->trade_window.front();
        impl_->rolling_notional -= evicted.notional;
        impl_->rolling_quantity -= evicted.quantity;
        impl_->rolling_signed_quantity -= evicted.signed_quantity;
        impl_->trade_window.pop_front();
    }
    if (impl_->rolling_quantity > 0.0) {
        row.rolling_vwap = impl_->rolling_notional / impl_->rolling_quantity;
        row.trade_flow_imbalance = impl_->rolling_signed_quantity / impl_->rolling_quantity;
    }

    if (snapshot.mid_price.has_value() && *snapshot.mid_price > 0.0) {
        impl_->mid_window.push_back(Impl::MidSample{message.timestamp, *snapshot.mid_price});
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

    return row;
}

std::vector<AnalyticsRow> replay_with_analytics(
    const std::vector<LobsterMessage>& messages,
    OrderBook& book,
    AnalyticsConfig config) {
    AnalyticsEngine engine(config);
    std::vector<AnalyticsRow> rows;
    rows.reserve(messages.size());
    for (const LobsterMessage& message : messages) {
        book.apply(message);
        rows.push_back(engine.on_message(message, book.snapshot(std::max<std::size_t>(config.depth_levels, 10))));
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
