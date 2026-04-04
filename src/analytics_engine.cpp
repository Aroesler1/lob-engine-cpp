#include "analytics_engine.h"

#include "lobster_parser.h"
#include "order_book.h"

#include <cmath>
#include <cstddef>
#include <limits>

namespace {

constexpr double kPriceScale = 10000.0;
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

double toOutputPrice(int64_t raw_price) {
    return static_cast<double>(raw_price) / kPriceScale;
}

template <typename LevelMap>
int64_t cumulativeDepth(const LevelMap& levels, std::size_t top_n) {
    int64_t total = 0;
    std::size_t count = 0;

    for (const auto& [price, level] : levels) {
        (void)price;
        total += level.total_size;
        ++count;
        if (count >= top_n) {
            break;
        }
    }

    return total;
}

double boundedImbalance(int64_t lhs, int64_t rhs) {
    const int64_t total = lhs + rhs;
    if (total <= 0) {
        return 0.0;
    }
    return static_cast<double>(lhs - rhs) / static_cast<double>(total);
}

}  // namespace

AnalyticsEngine::AnalyticsEngine(AnalyticsConfig config)
    : config_(config) {}

AnalyticsRecord AnalyticsEngine::onMessage(const LobsterMessage& message, const OrderBook& book) {
    appendTrade(message);
    trimMidWindow(message.timestamp);

    const auto best_bid = book.bestBid();
    const auto best_ask = book.bestAsk();
    if (best_bid && best_ask) {
        appendMid(message.timestamp, (toOutputPrice(*best_bid) + toOutputPrice(*best_ask)) / 2.0);
    }

    AnalyticsRecord record;
    record.timestamp = message.timestamp;

    if (best_bid) {
        record.best_bid = toOutputPrice(*best_bid);
    }
    if (best_ask) {
        record.best_ask = toOutputPrice(*best_ask);
    }
    if (best_bid && best_ask) {
        record.spread = toOutputPrice(*best_ask - *best_bid);
        record.mid = (record.best_bid + record.best_ask) / 2.0;
    }

    record.bid_depth_1 = cumulativeDepth(book.bids(), 1);
    record.ask_depth_1 = cumulativeDepth(book.asks(), 1);
    record.bid_depth_5 = cumulativeDepth(book.bids(), 5);
    record.ask_depth_5 = cumulativeDepth(book.asks(), 5);
    record.bid_depth_10 = cumulativeDepth(book.bids(), 10);
    record.ask_depth_10 = cumulativeDepth(book.asks(), 10);
    record.order_imbalance = boundedImbalance(record.bid_depth_10, record.ask_depth_10);

    if (trade_volume_sum_ > 0) {
        record.rolling_vwap = trade_notional_sum_ / static_cast<double>(trade_volume_sum_);
        record.trade_flow_imbalance =
            static_cast<double>(signed_trade_volume_sum_) / static_cast<double>(trade_volume_sum_);
    }

    if (mids_.size() >= 2) {
        record.rolling_realized_vol = std::sqrt(mid_return_square_sum_);
    } else {
        record.rolling_realized_vol = kNaN;
    }

    return record;
}

const AnalyticsConfig& AnalyticsEngine::config() const {
    return config_;
}

void AnalyticsEngine::appendTrade(const LobsterMessage& message) {
    if (config_.trade_window_messages == 0) {
        trades_.clear();
        trade_notional_sum_ = 0.0;
        trade_volume_sum_ = 0;
        signed_trade_volume_sum_ = 0;
        return;
    }

    if ((message.event_type != 4 && message.event_type != 5 && message.event_type != 6) ||
        message.size <= 0 || message.price <= 0) {
        return;
    }

    TradeEntry trade;
    trade.timestamp = message.timestamp;
    trade.price = toOutputPrice(message.price);
    trade.size = message.size;
    if (message.direction == 1 || message.direction == -1) {
        trade.signed_size = static_cast<int64_t>(message.direction) * message.size;
    }

    trades_.push_back(trade);
    trade_notional_sum_ += trade.price * static_cast<double>(trade.size);
    trade_volume_sum_ += trade.size;
    signed_trade_volume_sum_ += trade.signed_size;

    trimTradeWindow();
}

void AnalyticsEngine::trimTradeWindow() {
    while (trades_.size() > config_.trade_window_messages) {
        const TradeEntry& expired = trades_.front();
        trade_notional_sum_ -= expired.price * static_cast<double>(expired.size);
        trade_volume_sum_ -= expired.size;
        signed_trade_volume_sum_ -= expired.signed_size;
        trades_.pop_front();
    }

    if (trades_.empty()) {
        trade_notional_sum_ = 0.0;
        trade_volume_sum_ = 0;
        signed_trade_volume_sum_ = 0;
    }
}

void AnalyticsEngine::appendMid(double timestamp, double mid) {
    if (config_.vol_window_seconds <= 0.0) {
        mids_.clear();
        mid_return_square_sum_ = 0.0;
        return;
    }

    if (!mids_.empty()) {
        const double log_return = std::log(mid / mids_.back().mid);
        mid_return_square_sum_ += log_return * log_return;
    }

    mids_.push_back(MidPoint{timestamp, mid});
}

void AnalyticsEngine::trimMidWindow(double current_timestamp) {
    if (config_.vol_window_seconds <= 0.0) {
        mids_.clear();
        mid_return_square_sum_ = 0.0;
        return;
    }

    const double cutoff = current_timestamp - config_.vol_window_seconds;
    while (!mids_.empty() && mids_.front().timestamp < cutoff) {
        if (mids_.size() >= 2) {
            const double log_return = std::log(mids_[1].mid / mids_.front().mid);
            mid_return_square_sum_ -= log_return * log_return;
        }
        mids_.pop_front();
    }

    if (mids_.size() < 2) {
        mid_return_square_sum_ = 0.0;
        return;
    }

    if (mid_return_square_sum_ < 0.0) {
        mid_return_square_sum_ = 0.0;
    }
}
