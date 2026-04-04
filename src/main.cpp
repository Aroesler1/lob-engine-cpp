#include <array>
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

struct CliOptions {
    std::string filepath;
    std::string backend_name{"map"};
    std::size_t depth{5};
    std::size_t repeats{1};
};

void print_usage() {
    std::cerr << "Usage: lob_engine <lobster_csv_file> [--backend map|flat|both] [--depth N] [--repeat N]\n";
}

std::optional<std::size_t> parse_positive_size(const std::string& value) {
    if (value.empty() || value.front() == '-') {
        return std::nullopt;
    }

    char* end = nullptr;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (end == value.c_str() || *end != '\0') {
        return std::nullopt;
    }
    return static_cast<std::size_t>(parsed);
}

bool parse_args(int argc, char* argv[], CliOptions& options) {
    if (argc < 2) {
        return false;
    }

    options.filepath = argv[1];

    for (int index = 2; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "--backend") {
            if (index + 1 >= argc) {
                return false;
            }
            options.backend_name = argv[++index];
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

std::string format_level(const std::optional<lob::OrderBookLevel>& level) {
    if (!level.has_value()) {
        return "n/a";
    }

    return std::to_string(level->price) + " x " + std::to_string(level->total_size) +
           " (" + std::to_string(level->order_count) + ")";
}

}  // namespace

int main(int argc, char* argv[]) {
    CliOptions options;
    if (!parse_args(argc, argv, options)) {
        print_usage();
        return 1;
    }

    std::ifstream probe(options.filepath);
    if (!probe.is_open()) {
        std::cerr << "Could not open file: " << options.filepath << '\n';
        return 1;
    }

    const std::vector<lob::OrderBookBackend> backends = selected_backends(options.backend_name);
    if (backends.empty()) {
        std::cerr << "Unknown backend selector: " << options.backend_name << '\n';
        print_usage();
        return 1;
    }

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(options.filepath);

    std::array<std::size_t, 7> event_counts{};
    for (const lob::LobsterMessage& message : messages) {
        const std::size_t index = static_cast<std::size_t>(static_cast<int>(message.event_type) - 1);
        ++event_counts[index];
    }

    std::cout << "Parsed: " << messages.size() << '\n';
    std::cout << "Malformed: " << parser.malformed_count() << '\n';
    if (messages.empty()) {
        std::cout << "Time range: n/a\n";
    } else {
        std::cout << std::fixed << std::setprecision(6);
        std::cout << "Time range: " << messages.front().timestamp << " - " << messages.back().timestamp << '\n';
    }

    std::cout << "Event type counts:";
    for (std::size_t i = 0; i < event_counts.size(); ++i) {
        std::cout << ' ' << (i + 1) << ':' << event_counts[i];
    }
    std::cout << '\n';

    std::cout << std::fixed << std::setprecision(3);
    for (const lob::OrderBookBackend backend : backends) {
        const lob::ReplaySummary summary =
            lob::benchmark_replay(messages, backend, options.depth, options.repeats);
        std::cout << "Replay backend=" << lob::to_string(backend)
                  << " repeats=" << summary.repeats
                  << " processed=" << summary.processed_messages
                  << " elapsed_ms=" << summary.elapsed_ms
                  << " throughput_msgs_per_sec=" << summary.messages_per_second << '\n';
        std::cout << "Final top: bid=" << format_level(summary.final_snapshot.best_bid)
                  << " ask=" << format_level(summary.final_snapshot.best_ask)
                  << " active_orders=" << summary.final_snapshot.active_order_count << '\n';
    }

    return 0;
}
