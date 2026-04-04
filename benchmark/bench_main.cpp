#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "lob/parser.hpp"
#include "lob/types.hpp"

namespace {

std::filesystem::path source_root() {
    return std::filesystem::path(LOB_ENGINE_SOURCE_DIR);
}

struct Config {
    bool enable_analytics = true;
    bool enable_csv_export = false;
    std::size_t iterations = 0;
    std::filesystem::path data_path =
        source_root() / "data" / "AAPL_2024-01-02_message_10.csv";
    std::filesystem::path export_path =
        source_root() / "benchmark" / "results" / "aapl_replay_export.csv";
};

struct ReplayStats {
    std::array<std::size_t, 7> event_counts{};
    std::int64_t total_size = 0;
    std::int64_t notional = 0;
    double first_timestamp = 0.0;
    double last_timestamp = 0.0;
    std::uint64_t checksum = 1469598103934665603ULL;
    bool has_timestamps = false;
};

void print_usage(const char* program_name) {
    std::cerr << "Usage: " << program_name
              << " [--no-analytics] [--with-export] [--data <path>]"
                 " [--export-path <path>] [--iterations <count>]\n";
}

Config parse_args(int argc, char* argv[]) {
    Config config;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--no-analytics") {
            config.enable_analytics = false;
        } else if (arg == "--with-export") {
            config.enable_csv_export = true;
        } else if (arg == "--data") {
            if (i + 1 >= argc) {
                throw std::invalid_argument("missing value for --data");
            }
            config.data_path = argv[++i];
        } else if (arg == "--export-path") {
            if (i + 1 >= argc) {
                throw std::invalid_argument("missing value for --export-path");
            }
            config.export_path = argv[++i];
        } else if (arg == "--iterations") {
            if (i + 1 >= argc) {
                throw std::invalid_argument("missing value for --iterations");
            }
            config.iterations = static_cast<std::size_t>(std::stoull(argv[++i]));
        } else if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            std::exit(0);
        } else {
            throw std::invalid_argument("unknown argument: " + arg);
        }
    }
    return config;
}

std::uint64_t mix_checksum(std::uint64_t checksum, const lob::LobsterMessage& msg) {
    constexpr std::uint64_t kFnvPrime = 1099511628211ULL;
    const std::uint64_t event_value = static_cast<std::uint64_t>(static_cast<int>(msg.event_type));
    const std::uint64_t side_value = msg.direction == lob::Side::Buy ? 1ULL : 2ULL;

    checksum ^= static_cast<std::uint64_t>(msg.order_id);
    checksum *= kFnvPrime;
    checksum ^= static_cast<std::uint64_t>(msg.size);
    checksum *= kFnvPrime;
    checksum ^= static_cast<std::uint64_t>(msg.price);
    checksum *= kFnvPrime;
    checksum ^= event_value;
    checksum *= kFnvPrime;
    checksum ^= side_value;
    checksum *= kFnvPrime;
    checksum ^= static_cast<std::uint64_t>(msg.timestamp * 1000000.0);
    checksum *= kFnvPrime;
    return checksum;
}

void update_analytics(const lob::LobsterMessage& msg, ReplayStats& stats) {
    const std::size_t index = static_cast<std::size_t>(static_cast<int>(msg.event_type) - 1);
    ++stats.event_counts[index];
    stats.total_size += msg.size;
    stats.notional += static_cast<std::int64_t>(msg.size) * msg.price;

    if (!stats.has_timestamps) {
        stats.first_timestamp = msg.timestamp;
        stats.last_timestamp = msg.timestamp;
        stats.has_timestamps = true;
    } else {
        stats.last_timestamp = msg.timestamp;
    }
}

void write_export_header(std::ofstream& output) {
    output << "timestamp,event_type,order_id,size,price,direction,checksum\n";
}

void write_export_row(std::ofstream& output,
                      const lob::LobsterMessage& msg,
                      std::uint64_t checksum) {
    const int direction = msg.direction == lob::Side::Buy ? 1 : -1;
    output << std::fixed << std::setprecision(6) << msg.timestamp << ','
           << static_cast<int>(msg.event_type) << ',' << msg.order_id << ',' << msg.size
           << ',' << msg.price << ',' << direction << ',' << checksum << '\n';
}

ReplayStats replay_messages(const std::vector<lob::LobsterMessage>& messages,
                            bool enable_analytics,
                            std::ofstream* export_stream,
                            std::size_t iterations) {
    ReplayStats stats;
    for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
        for (const lob::LobsterMessage& msg : messages) {
            stats.checksum = mix_checksum(stats.checksum, msg);
            if (enable_analytics) {
                update_analytics(msg, stats);
            }
            if (export_stream != nullptr) {
                write_export_row(*export_stream, msg, stats.checksum);
            }
        }
    }
    return stats;
}

std::size_t resolve_iterations(const Config& config, std::size_t message_count) {
    if (config.iterations != 0) {
        return config.iterations;
    }
    if (config.enable_csv_export) {
        return 1;
    }

    constexpr std::size_t kTargetTimedMessages = 20000000;
    const std::size_t safe_message_count = message_count == 0 ? 1 : message_count;
    const std::size_t auto_iterations =
        (kTargetTimedMessages + safe_message_count - 1) / safe_message_count;
    return auto_iterations == 0 ? 1 : auto_iterations;
}

void print_event_counts(const std::array<std::size_t, 7>& event_counts) {
    std::cout << "Event counts across timed iterations:";
    for (std::size_t i = 0; i < event_counts.size(); ++i) {
        std::cout << ' ' << (i + 1) << ':' << event_counts[i];
    }
    std::cout << '\n';
}

}  // namespace

int main(int argc, char* argv[]) {
    Config config;
    try {
        config = parse_args(argc, argv);
    } catch (const std::exception& ex) {
        std::cerr << ex.what() << '\n';
        print_usage(argv[0]);
        return 1;
    }

    if (!std::filesystem::exists(config.data_path)) {
        std::cerr << "Data file does not exist: " << config.data_path << '\n';
        return 1;
    }

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(config.data_path.string());
    if (messages.empty()) {
        std::cerr << "No valid messages loaded from " << config.data_path << '\n';
        return 1;
    }

    const std::size_t timed_iterations = resolve_iterations(config, messages.size());
    const ReplayStats warm_up =
        replay_messages(messages, config.enable_analytics, nullptr, 1);
    (void)warm_up;

    std::ofstream export_stream;
    if (config.enable_csv_export) {
        std::filesystem::create_directories(config.export_path.parent_path());
        export_stream.open(config.export_path);
        if (!export_stream.is_open()) {
            std::cerr << "Could not open export file: " << config.export_path << '\n';
            return 1;
        }
        write_export_header(export_stream);
    }

    const auto start = std::chrono::high_resolution_clock::now();
    const ReplayStats stats = replay_messages(
        messages,
        config.enable_analytics,
        config.enable_csv_export ? &export_stream : nullptr,
        timed_iterations);
    const auto end = std::chrono::high_resolution_clock::now();

    const double elapsed_s = std::chrono::duration<double>(end - start).count();
    const std::size_t total_messages = messages.size() * timed_iterations;
    const double throughput =
        elapsed_s > 0.0 ? static_cast<double>(total_messages) / elapsed_s : 0.0;

    std::cout << "Data file: " << config.data_path << '\n';
    std::cout << "Messages per iteration: " << messages.size() << '\n';
    std::cout << "Timed iterations: " << timed_iterations << '\n';
    std::cout << "Messages processed: " << total_messages << '\n';
    std::cout << "Malformed while loading: " << parser.malformed_count() << '\n';
    std::cout << std::fixed << std::setprecision(6);
    std::cout << "Elapsed time: " << elapsed_s << " s\n";
    std::cout << std::setprecision(2);
    std::cout << "Throughput: " << throughput << " msgs/s\n";
    std::cout << "Analytics enabled: " << (config.enable_analytics ? "yes" : "no") << '\n';
    std::cout << "CSV export enabled: " << (config.enable_csv_export ? "yes" : "no") << '\n';
    if (config.enable_csv_export) {
        std::cout << "Export path: " << config.export_path << '\n';
    }
    std::cout << "Checksum: " << stats.checksum << '\n';

    if (config.enable_analytics) {
        if (stats.has_timestamps) {
            std::cout << std::setprecision(6);
            std::cout << "Time range: " << stats.first_timestamp << " - " << stats.last_timestamp
                      << '\n';
        } else {
            std::cout << "Time range: n/a\n";
        }
        std::cout << "Total size across timed iterations: " << stats.total_size << '\n';
        std::cout << "Total notional across timed iterations (price x 10000): " << stats.notional
                  << '\n';
        print_event_counts(stats.event_counts);
    }

    return 0;
}
