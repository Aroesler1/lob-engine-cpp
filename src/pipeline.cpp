#include "lob/pipeline.hpp"

#include <chrono>

#include "lob/level_containers.hpp"
#include "lob/order_book.hpp"

namespace lob {

namespace {

using Clock = std::chrono::high_resolution_clock;

template <typename BookType>
PipelineResult run_pipeline_messages_impl(const std::vector<LobsterMessage>& messages, const PipelineOptions& options) {
    BookType book;
    if (options.reserve_orders_hint > 0) {
        book.reserve_orders(options.reserve_orders_hint);
    } else if (!messages.empty()) {
        book.reserve_orders(messages.size() / 3);
    }

    PipelineResult result{};
    if (options.store_analytics_rows) {
        result.analytics_rows.reserve(messages.size());
    }
    if (options.capture_message_latencies) {
        result.message_latencies_ns.reserve(messages.size());
    }

    const auto start = Clock::now();
    std::size_t sequence = 0;
    for (const LobsterMessage& message : messages) {
        if (sequence == 0) {
            result.first_timestamp = message.timestamp;
        }
        result.last_timestamp = message.timestamp;

        const auto message_start = options.capture_message_latencies ? Clock::now() : Clock::time_point{};
        book.process(message);
        ++sequence;
        const AnalyticsRow row = book.analytics_row(sequence, message);
        if (options.store_analytics_rows) {
            result.analytics_rows.push_back(row);
        }
        if (options.capture_message_latencies) {
            const auto message_end = Clock::now();
            result.message_latencies_ns.push_back(
                std::chrono::duration_cast<std::chrono::nanoseconds>(message_end - message_start).count());
        }
    }
    const auto end = Clock::now();

    const BookSnapshot snapshot = book.snapshot();
    result.parsed_messages = messages.size();
    result.peak_active_levels = book.peak_active_levels();
    result.peak_visible_orders = book.peak_visible_orders();
    result.final_bid_levels = snapshot.bid_levels;
    result.final_ask_levels = snapshot.ask_levels;
    result.final_total_orders = snapshot.total_orders;
    result.approximate_memory_bytes = book.approximate_memory_bytes();
    result.wall_clock_seconds =
        std::chrono::duration_cast<std::chrono::duration<double>>(end - start).count();
    return result;
}

template <typename BookType>
PipelineResult run_pipeline_file_impl(const std::string& filepath, const PipelineOptions& options) {
    LobsterParser parser;
    PipelineResult result{};
    if (!parser.open(filepath)) {
        return result;
    }

    BookType book;
    if (options.reserve_orders_hint > 0) {
        book.reserve_orders(options.reserve_orders_hint);
    }

    LobsterMessage message{};
    const auto start = Clock::now();
    std::size_t sequence = 0;
    while (parser.next(message)) {
        if (sequence == 0) {
            result.first_timestamp = message.timestamp;
        }
        result.last_timestamp = message.timestamp;

        const auto message_start = options.capture_message_latencies ? Clock::now() : Clock::time_point{};
        book.process(message);
        ++sequence;
        const AnalyticsRow row = book.analytics_row(sequence, message);
        if (options.store_analytics_rows) {
            result.analytics_rows.push_back(row);
        }
        if (options.capture_message_latencies) {
            const auto message_end = Clock::now();
            result.message_latencies_ns.push_back(
                std::chrono::duration_cast<std::chrono::nanoseconds>(message_end - message_start).count());
        }
    }
    const auto end = Clock::now();

    const BookSnapshot snapshot = book.snapshot();
    result.parsed_messages = parser.parsed_count();
    result.malformed_messages = parser.malformed_count();
    result.peak_active_levels = book.peak_active_levels();
    result.peak_visible_orders = book.peak_visible_orders();
    result.final_bid_levels = snapshot.bid_levels;
    result.final_ask_levels = snapshot.ask_levels;
    result.final_total_orders = snapshot.total_orders;
    result.approximate_memory_bytes = book.approximate_memory_bytes();
    result.wall_clock_seconds =
        std::chrono::duration_cast<std::chrono::duration<double>>(end - start).count();
    return result;
}

using MapBook = OrderBook<MapLevelContainer<Side::Buy>, MapLevelContainer<Side::Sell>>;
using FlatBook = OrderBook<FlatLevelContainer<Side::Buy>, FlatLevelContainer<Side::Sell>>;

}  // namespace

ParsedMessages load_messages(const std::string& filepath) {
    LobsterParser parser;
    ParsedMessages parsed{};
    parsed.messages = parser.parse_file(filepath);
    parsed.malformed_messages = parser.malformed_count();
    return parsed;
}

PipelineResult run_pipeline_messages(const std::vector<LobsterMessage>& messages, const PipelineOptions& options) {
    if (options.container_type == ContainerType::FlatVector) {
        return run_pipeline_messages_impl<FlatBook>(messages, options);
    }
    return run_pipeline_messages_impl<MapBook>(messages, options);
}

PipelineResult run_pipeline_file(const std::string& filepath, const PipelineOptions& options) {
    if (options.container_type == ContainerType::FlatVector) {
        return run_pipeline_file_impl<FlatBook>(filepath, options);
    }
    return run_pipeline_file_impl<MapBook>(filepath, options);
}

}  // namespace lob
