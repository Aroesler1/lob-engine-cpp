#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "lob/analytics.hpp"
#include "lob/parser.hpp"
#include "lob/types.hpp"

namespace lob {

struct ParsedMessages {
    std::vector<LobsterMessage> messages;
    std::size_t malformed_messages{0};
};

struct PipelineOptions {
    ContainerType container_type{ContainerType::StdMap};
    bool store_analytics_rows{false};
    bool capture_message_latencies{false};
    std::size_t reserve_orders_hint{0};
};

struct PipelineResult {
    std::size_t parsed_messages{0};
    std::size_t malformed_messages{0};
    std::vector<AnalyticsRow> analytics_rows;
    std::vector<std::int64_t> message_latencies_ns;
    std::size_t peak_active_levels{0};
    std::size_t peak_visible_orders{0};
    std::size_t final_bid_levels{0};
    std::size_t final_ask_levels{0};
    std::size_t final_total_orders{0};
    std::size_t approximate_memory_bytes{0};
    double first_timestamp{0.0};
    double last_timestamp{0.0};
    double wall_clock_seconds{0.0};
};

ParsedMessages load_messages(const std::string& filepath);
PipelineResult run_pipeline_messages(const std::vector<LobsterMessage>& messages, const PipelineOptions& options);
PipelineResult run_pipeline_file(const std::string& filepath, const PipelineOptions& options);

}  // namespace lob
