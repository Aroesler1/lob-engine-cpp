#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include "lob/order_book.hpp"
#include "lob/parser.hpp"
#include "lob/replay.hpp"

namespace {

struct BenchmarkOptions {
    std::string dataset{"data/AAPL_sample_messages.csv"};
    std::string backend_name{"both"};
    std::string reserve_mode{"both"};
    std::size_t depth{5};
    std::size_t repeats{100000};
};

void print_usage() {
    std::cerr
        << "Usage: lob_benchmark [--dataset PATH] [--backend map|flat|both] [--reserve on|off|both] "
           "[--depth N] [--repeat N]\n";
}

std::optional<std::size_t> parse_positive_size(const std::string& value) {
    if (value.empty() || value.front() == '-') {
        return std::nullopt;
    }

    char* end = nullptr;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (end == value.c_str() || *end != '\0' || parsed == 0) {
        return std::nullopt;
    }
    return static_cast<std::size_t>(parsed);
}

bool parse_args(int argc, char* argv[], BenchmarkOptions& options) {
    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "--dataset") {
            if (index + 1 >= argc) {
                return false;
            }
            options.dataset = argv[++index];
            continue;
        }
        if (arg == "--backend") {
            if (index + 1 >= argc) {
                return false;
            }
            options.backend_name = argv[++index];
            continue;
        }
        if (arg == "--reserve") {
            if (index + 1 >= argc) {
                return false;
            }
            options.reserve_mode = argv[++index];
            continue;
        }
        if (arg == "--depth") {
            if (index + 1 >= argc) {
                return false;
            }
            const auto parsed = parse_positive_size(argv[++index]);
            if (!parsed.has_value()) {
                return false;
            }
            options.depth = *parsed;
            continue;
        }
        if (arg == "--repeat") {
            if (index + 1 >= argc) {
                return false;
            }
            const auto parsed = parse_positive_size(argv[++index]);
            if (!parsed.has_value()) {
                return false;
            }
            options.repeats = *parsed;
            continue;
        }
        return false;
    }
    return true;
}

std::optional<std::string> resolve_dataset_path(const std::string& dataset) {
    std::ifstream direct_probe(dataset);
    if (direct_probe.is_open()) {
        return dataset;
    }
    const std::string source_relative = std::string(LOB_ENGINE_SOURCE_DIR) + "/" + dataset;
    std::ifstream source_probe(source_relative);
    if (source_probe.is_open()) {
        return source_relative;
    }
    return std::nullopt;
}

std::vector<lob::OrderBookBackend> selected_backends(const std::string& backend_name) {
    if (backend_name == "both") {
        return {lob::OrderBookBackend::Map, lob::OrderBookBackend::FlatVector};
    }
    const auto parsed = lob::parse_order_book_backend(backend_name);
    if (!parsed.has_value()) {
        return {};
    }
    return {*parsed};
}

std::vector<bool> selected_preallocation_modes(const std::string& mode) {
    if (mode == "both") {
        return {false, true};
    }
    if (mode == "on") {
        return {true};
    }
    if (mode == "off") {
        return {false};
    }
    return {};
}

std::string format_level(const std::optional<lob::OrderBookLevel>& level) {
    if (!level.has_value()) {
        return "n/a";
    }
    return std::to_string(level->price) + " x " + std::to_string(level->total_size) +
           " (" + std::to_string(level->order_count) + ")";
}

double average_ns_per_message(const lob::ReplaySummary& summary) {
    if (summary.processed_messages == 0) {
        return 0.0;
    }
    return (summary.elapsed_ms * 1000000.0) / static_cast<double>(summary.processed_messages);
}

}  // namespace

int main(int argc, char* argv[]) {
    BenchmarkOptions options;
    if (!parse_args(argc, argv, options)) {
        print_usage();
        return 1;
    }

    const auto resolved_dataset = resolve_dataset_path(options.dataset);
    if (!resolved_dataset.has_value()) {
        std::cerr << "Could not open dataset: " << options.dataset << '\n';
        return 1;
    }

    const std::vector<lob::OrderBookBackend> backends = selected_backends(options.backend_name);
    if (backends.empty()) {
        std::cerr << "Unknown backend selector: " << options.backend_name << '\n';
        return 1;
    }
    const std::vector<bool> reserve_modes = selected_preallocation_modes(options.reserve_mode);
    if (reserve_modes.empty()) {
        std::cerr << "Unknown reserve selector: " << options.reserve_mode << '\n';
        return 1;
    }

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(*resolved_dataset);

    std::cout << std::fixed << std::setprecision(3);
    std::cout << "Benchmark results\n";
    std::cout << "Dataset argument: " << options.dataset << '\n';
    std::cout << "Dataset path used: " << *resolved_dataset << '\n';
    std::cout << "Parsed messages: " << messages.size() << '\n';
    std::cout << "Malformed messages: " << parser.malformed_count() << '\n';
    std::cout << "Depth: " << options.depth << '\n';
    std::cout << "Repeat count: " << options.repeats << '\n';

    for (bool enable_preallocation : reserve_modes) {
        lob::OrderBookBuildConfig build_config;
        build_config.expected_orders = messages.size();
        build_config.expected_levels_per_side = 64;
        build_config.enable_preallocation = enable_preallocation;

        for (const lob::OrderBookBackend backend : backends) {
            const lob::ReplaySummary summary = lob::benchmark_replay(
                messages,
                backend,
                options.depth,
                options.repeats,
                build_config);
            std::cout << "Replay backend=" << lob::to_string(backend)
                      << " reserve=" << (enable_preallocation ? "on" : "off")
                      << " repeats=" << summary.repeats
                      << " processed=" << summary.processed_messages
                      << " elapsed_ms=" << summary.elapsed_ms
                      << " throughput_msgs_per_sec=" << summary.messages_per_second
                      << " avg_ns_per_msg=" << average_ns_per_message(summary) << '\n';
            std::cout << "Final top: bid=" << format_level(summary.final_snapshot.best_bid)
                      << " ask=" << format_level(summary.final_snapshot.best_ask)
                      << " active_orders=" << summary.final_snapshot.active_order_count << '\n';
        }
    }

    return 0;
}
