#pragma once

#include <cstddef>
#include <initializer_list>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "lob/order_book.hpp"
#include "lob/types.hpp"

namespace lob {

struct OptionalStringSetting {
    OptionalStringSetting() = default;
    OptionalStringSetting(std::nullopt_t) noexcept {}
    OptionalStringSetting(const std::optional<std::string>& text)
        : value_(text) {}
    OptionalStringSetting(std::optional<std::string>&& text) noexcept
        : value_(std::move(text)) {}
    OptionalStringSetting(const std::string& text)
        : value_(text) {}
    OptionalStringSetting(std::string&& text) noexcept
        : value_(std::move(text)) {}
    OptionalStringSetting(const char* text)
        : value_(text == nullptr ? std::optional<std::string>{} : std::optional<std::string>{text}) {}

    OptionalStringSetting& operator=(std::nullopt_t) noexcept {
        value_.reset();
        return *this;
    }

    OptionalStringSetting& operator=(const std::optional<std::string>& text) {
        value_ = text;
        return *this;
    }

    OptionalStringSetting& operator=(std::optional<std::string>&& text) noexcept {
        value_ = std::move(text);
        return *this;
    }

    OptionalStringSetting& operator=(const std::string& text) {
        value_ = text;
        return *this;
    }

    OptionalStringSetting& operator=(std::string&& text) noexcept {
        value_ = std::move(text);
        return *this;
    }

    OptionalStringSetting& operator=(const char* text) {
        value_ = text == nullptr ? std::optional<std::string>{} : std::optional<std::string>{text};
        return *this;
    }

    bool has_value() const noexcept {
        return value_.has_value();
    }

    bool empty() const noexcept {
        return !value_.has_value() || value_->empty();
    }

    void reset() noexcept {
        value_.reset();
    }

    const std::string& value() const {
        return value_.value();
    }

    std::string value_or(std::string default_value) const {
        return value_.value_or(std::move(default_value));
    }

    const std::string& operator*() const {
        return value();
    }

    std::string& operator*() {
        return value_.value();
    }

    const std::string* operator->() const {
        return &value();
    }

    std::string* operator->() {
        return &value_.value();
    }

    explicit operator bool() const noexcept {
        return value_.has_value();
    }

    friend bool operator==(const OptionalStringSetting& lhs, std::nullopt_t) noexcept {
        return !lhs.value_.has_value();
    }

    friend bool operator==(std::nullopt_t, const OptionalStringSetting& rhs) noexcept {
        return rhs == std::nullopt;
    }

    friend bool operator!=(const OptionalStringSetting& lhs, std::nullopt_t) noexcept {
        return !(lhs == std::nullopt);
    }

    friend bool operator!=(std::nullopt_t, const OptionalStringSetting& rhs) noexcept {
        return !(rhs == std::nullopt);
    }

    friend bool operator==(const OptionalStringSetting& lhs, const std::string& rhs) {
        return lhs.value_ == rhs;
    }

    friend bool operator==(const std::string& lhs, const OptionalStringSetting& rhs) {
        return rhs == lhs;
    }

    friend bool operator!=(const OptionalStringSetting& lhs, const std::string& rhs) {
        return !(lhs == rhs);
    }

    friend bool operator!=(const std::string& lhs, const OptionalStringSetting& rhs) {
        return !(rhs == lhs);
    }

    friend bool operator==(const OptionalStringSetting& lhs, const char* rhs) {
        return lhs == std::string(rhs == nullptr ? "" : rhs);
    }

    friend bool operator==(const char* lhs, const OptionalStringSetting& rhs) {
        return rhs == lhs;
    }

    friend bool operator!=(const OptionalStringSetting& lhs, const char* rhs) {
        return !(lhs == rhs);
    }

    friend bool operator!=(const char* lhs, const OptionalStringSetting& rhs) {
        return !(rhs == lhs);
    }

    friend bool operator==(const OptionalStringSetting& lhs, const std::optional<std::string>& rhs) {
        return lhs.value_ == rhs;
    }

    friend bool operator==(const std::optional<std::string>& lhs, const OptionalStringSetting& rhs) {
        return rhs == lhs;
    }

    friend bool operator!=(const OptionalStringSetting& lhs, const std::optional<std::string>& rhs) {
        return !(lhs == rhs);
    }

    friend bool operator!=(const std::optional<std::string>& lhs, const OptionalStringSetting& rhs) {
        return !(rhs == lhs);
    }

private:
    std::optional<std::string> value_{};
};

struct AnalyticsConfig {
    std::size_t trade_window_messages{1000};
    double realized_vol_window_seconds{300.0};
    std::size_t depth_levels{10};
    std::size_t expected_messages{0};
    std::vector<std::size_t> prediction_horizons{};
    OptionalStringSetting prediction_report_out{};
    std::vector<int> prediction_horizons_messages{};

    bool prediction_report_output_enabled() const noexcept {
        return prediction_report_out.has_value() && !prediction_report_out.empty();
    }

    std::vector<std::size_t> resolved_prediction_horizons() const {
        const bool use_message_horizons = !prediction_horizons_messages.empty();
        std::vector<std::size_t> horizons;

        if (use_message_horizons) {
            horizons.reserve(prediction_horizons_messages.size());
            for (const int horizon : prediction_horizons_messages) {
                if (horizon > 0) {
                    horizons.push_back(static_cast<std::size_t>(horizon));
                }
            }
            return horizons;
        }

        horizons.reserve(prediction_horizons.size());
        for (const std::size_t horizon : prediction_horizons) {
            if (horizon > 0 &&
                horizon <= static_cast<std::size_t>(std::numeric_limits<int>::max())) {
                horizons.push_back(horizon);
            }
        }
        return horizons;
    }

    std::vector<int> resolved_prediction_horizons_messages() const {
        const std::vector<std::size_t> resolved_horizons = resolved_prediction_horizons();
        std::vector<int> horizons;
        horizons.reserve(resolved_horizons.size());
        for (const std::size_t horizon : resolved_horizons) {
            horizons.push_back(static_cast<int>(horizon));
        }
        return horizons;
    }

    bool prediction_reporting_enabled() const {
        return prediction_report_output_enabled() && !resolved_prediction_horizons().empty();
    }
};

struct AnalyticsRow {
    double timestamp{0.0};
    std::optional<Price> best_bid;
    std::optional<Price> best_ask;
    std::optional<Price> spread;
    std::optional<double> mid_price;
    AggregateQuantity bid_depth_1{0};
    AggregateQuantity bid_depth_5{0};
    AggregateQuantity bid_depth_10{0};
    AggregateQuantity ask_depth_1{0};
    AggregateQuantity ask_depth_5{0};
    AggregateQuantity ask_depth_10{0};
    std::optional<double> order_imbalance;
    std::optional<double> rolling_vwap;
    std::optional<double> trade_flow_imbalance;
    std::optional<double> rolling_realized_vol;
};

struct PredictionSnapshot {
    std::size_t message_index{0};
    std::optional<double> mid_price;
    std::optional<double> order_imbalance_top5;
};

struct PredictionSummaryRow {
    std::size_t horizon_messages{0};
    std::size_t total_rows_seen{0};
    std::size_t eligible_rows_with_valid_mid{0};
    std::size_t labeled_rows{0};
    std::size_t skipped_no_valid_mid{0};
    std::size_t skipped_no_future_move_within_horizon{0};
    std::size_t skipped_zero_signal{0};
    std::size_t up_moves{0};
    std::size_t down_moves{0};
    std::size_t correct_predictions{0};
    std::size_t incorrect_predictions{0};
    double hit_rate{0.0};
    double information_coefficient{0.0};
    double coverage_vs_total{0.0};
};

class AnalyticsEngine {
public:
    explicit AnalyticsEngine(AnalyticsConfig config = {});
    ~AnalyticsEngine();

    AnalyticsEngine(AnalyticsEngine&&) noexcept;
    AnalyticsEngine& operator=(AnalyticsEngine&&) noexcept;

    AnalyticsEngine(const AnalyticsEngine&) = delete;
    AnalyticsEngine& operator=(const AnalyticsEngine&) = delete;

    AnalyticsRow on_message(const LobsterMessage& message, const BookSnapshot& snapshot);
    void on_message(const LobsterMessage& message, const BookSnapshot& snapshot, AnalyticsRow& row);
    void reset();

private:
    AnalyticsConfig config_;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

std::vector<AnalyticsRow> replay_with_analytics(
    const std::vector<LobsterMessage>& messages,
    OrderBook& book,
    AnalyticsConfig config = {});

void write_analytics_csv(const std::vector<AnalyticsRow>& rows, const std::string& output_path);

std::vector<PredictionSnapshot> collect_prediction_snapshots(const std::vector<AnalyticsRow>& rows);

std::vector<PredictionSummaryRow> summarize_prediction_horizons(
    const std::vector<PredictionSnapshot>& snapshots,
    const std::vector<std::size_t>& horizons);

std::vector<PredictionSummaryRow> summarize_prediction_horizons(
    const std::vector<PredictionSnapshot>& snapshots,
    const std::vector<int>& horizons);

std::vector<PredictionSummaryRow> summarize_prediction_horizons(
    const std::vector<PredictionSnapshot>& snapshots,
    std::initializer_list<int> horizons);

void write_prediction_report_csv(
    const std::vector<PredictionSummaryRow>& rows,
    const std::string& output_path);

}  // namespace lob
