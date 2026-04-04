#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "lob/pipeline.hpp"
#include "lob/types.hpp"

namespace {

struct Config {
    std::string data_file;
    std::string output_file;
    std::string ticker_override;
    lob::ContainerType container_type{lob::ContainerType::StdMap};
    std::size_t iterations{5};
    std::size_t warmup{1};
};

double bytes_to_mib(std::size_t bytes) {
    return static_cast<double>(bytes) / (1024.0 * 1024.0);
}

std::string detect_ticker(const std::string& filepath, const std::string& override_ticker) {
    if (!override_ticker.empty()) {
        return override_ticker;
    }

    std::string stem = std::filesystem::path(filepath).stem().string();
    const std::size_t message_pos = stem.find("_message");
    if (message_pos != std::string::npos) {
        stem = stem.substr(0, message_pos);
    }
    if (stem.rfind("AAPL_", 0) == 0) {
        return "AAPL";
    }
    return stem;
}

std::optional<long long> read_status_kib(const std::string& key) {
    std::ifstream input("/proc/self/status");
    if (!input.is_open()) {
        return std::nullopt;
    }

    std::string label;
    long long value = 0;
    std::string unit;
    while (input >> label >> value >> unit) {
        if (label == key) {
            return value;
        }
    }
    return std::nullopt;
}

std::int64_t percentile(std::vector<std::int64_t> values, double pct) {
    if (values.empty()) {
        return 0;
    }

    std::sort(values.begin(), values.end());
    const double scaled_index = pct * static_cast<double>(values.size() - 1);
    const std::size_t index = static_cast<std::size_t>(scaled_index);
    return values[index];
}

void append_csv_row(
    const std::string& filepath,
    const std::string& header,
    const std::string& row) {
    const bool needs_header =
        !std::filesystem::exists(filepath) || std::filesystem::file_size(filepath) == 0;
    std::ofstream output(filepath, std::ios::app);
    if (!output.is_open()) {
        throw std::runtime_error("could not open benchmark output file");
    }
    if (needs_header) {
        output << header << '\n';
    }
    output << row << '\n';
}

Config parse_args(int argc, char* argv[]) {
    Config config{};

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--data" && i + 1 < argc) {
            config.data_file = argv[++i];
            continue;
        }
        if (arg == "--container" && i + 1 < argc) {
            if (!lob::parse_container_type(argv[++i], config.container_type)) {
                throw std::invalid_argument("unknown container type");
            }
            continue;
        }
        if (arg == "--iterations" && i + 1 < argc) {
            config.iterations = static_cast<std::size_t>(std::stoul(argv[++i]));
            continue;
        }
        if (arg == "--warmup" && i + 1 < argc) {
            config.warmup = static_cast<std::size_t>(std::stoul(argv[++i]));
            continue;
        }
        if (arg == "--output" && i + 1 < argc) {
            config.output_file = argv[++i];
            continue;
        }
        if (arg == "--ticker" && i + 1 < argc) {
            config.ticker_override = argv[++i];
            continue;
        }
        if (arg == "--help") {
            throw std::invalid_argument("");
        }
        throw std::invalid_argument("unrecognized argument: " + arg);
    }

    if (config.data_file.empty()) {
        throw std::invalid_argument("missing --data");
    }
    if (config.iterations == 0) {
        throw std::invalid_argument("--iterations must be greater than zero");
    }

    return config;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const Config config = parse_args(argc, argv);
        const std::string ticker = detect_ticker(config.data_file, config.ticker_override);

        lob::PipelineOptions warmup_options{};
        warmup_options.container_type = config.container_type;

        for (std::size_t iteration = 0; iteration < config.warmup; ++iteration) {
            (void)lob::run_pipeline_file(config.data_file, warmup_options);
        }

        std::vector<double> throughputs;
        std::vector<double> wall_clock_seconds;
        std::vector<std::int64_t> latencies_ns;
        throughputs.reserve(config.iterations);
        wall_clock_seconds.reserve(config.iterations);

        std::size_t messages = 0;
        std::size_t malformed = 0;
        std::size_t peak_active_levels = 0;
        std::size_t peak_visible_orders = 0;
        std::size_t approximate_memory_bytes = 0;

        lob::PipelineOptions benchmark_options{};
        benchmark_options.container_type = config.container_type;
        benchmark_options.capture_message_latencies = true;

        for (std::size_t iteration = 0; iteration < config.iterations; ++iteration) {
            lob::PipelineResult result = lob::run_pipeline_file(config.data_file, benchmark_options);
            messages = result.parsed_messages;
            malformed = result.malformed_messages;
            peak_active_levels = std::max(peak_active_levels, result.peak_active_levels);
            peak_visible_orders = std::max(peak_visible_orders, result.peak_visible_orders);
            approximate_memory_bytes = std::max(approximate_memory_bytes, result.approximate_memory_bytes);
            wall_clock_seconds.push_back(result.wall_clock_seconds);
            throughputs.push_back(
                result.wall_clock_seconds > 0.0
                    ? static_cast<double>(result.parsed_messages) / result.wall_clock_seconds
                    : 0.0);
            latencies_ns.insert(
                latencies_ns.end(),
                result.message_latencies_ns.begin(),
                result.message_latencies_ns.end());
        }

        if (messages == 0) {
            std::cerr << "No parsed messages for " << config.data_file << '\n';
            return 1;
        }

        const double throughput_mean =
            std::accumulate(throughputs.begin(), throughputs.end(), 0.0) / throughputs.size();
        const auto throughput_minmax = std::minmax_element(throughputs.begin(), throughputs.end());
        const double wall_clock_mean =
            std::accumulate(wall_clock_seconds.begin(), wall_clock_seconds.end(), 0.0) /
            wall_clock_seconds.size();

        const double approx_memory_mb = bytes_to_mib(approximate_memory_bytes);
        const std::optional<long long> peak_rss_kib = read_status_kib("VmHWM:");
        const double peak_rss_mb =
            peak_rss_kib.has_value() ? static_cast<double>(*peak_rss_kib) / 1024.0 : 0.0;

        const std::int64_t p50_ns = percentile(latencies_ns, 0.50);
        const std::int64_t p95_ns = percentile(latencies_ns, 0.95);
        const std::int64_t p99_ns = percentile(latencies_ns, 0.99);

        std::cout << std::fixed << std::setprecision(2);
        std::cout << "ticker=" << ticker
                  << " container=" << lob::container_type_name(config.container_type)
                  << " messages=" << messages
                  << " peak_levels=" << peak_active_levels
                  << " throughput_mean_msg_s=" << throughput_mean
                  << " p50_ns=" << p50_ns
                  << " p99_ns=" << p99_ns
                  << " approx_memory_mb=" << approx_memory_mb
                  << '\n';

        if (!config.output_file.empty()) {
            const std::string header =
                "ticker,data_file,container,messages,malformed,peak_active_levels,peak_visible_orders,"
                "mean_throughput_msg_s,min_throughput_msg_s,max_throughput_msg_s,"
                "p50_latency_ns,p95_latency_ns,p99_latency_ns,approx_memory_mb,peak_rss_mb,"
                "iterations,warmup,mean_wall_clock_s";
            std::ostringstream row;
            row << std::fixed << std::setprecision(6)
                << ticker << ','
                << config.data_file << ','
                << lob::container_type_name(config.container_type) << ','
                << messages << ','
                << malformed << ','
                << peak_active_levels << ','
                << peak_visible_orders << ','
                << throughput_mean << ','
                << *throughput_minmax.first << ','
                << *throughput_minmax.second << ','
                << p50_ns << ','
                << p95_ns << ','
                << p99_ns << ','
                << approx_memory_mb << ','
                << peak_rss_mb << ','
                << config.iterations << ','
                << config.warmup << ','
                << wall_clock_mean;
            append_csv_row(config.output_file, header, row.str());
        }

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Usage: run_benchmarks --data <file> --container map|flat_vec "
                     "[--iterations 5] [--warmup 1] [--output results.csv] [--ticker NAME]\n";
        if (std::string(ex.what()).empty()) {
            return 1;
        }
        std::cerr << ex.what() << '\n';
        return 1;
    }
}
