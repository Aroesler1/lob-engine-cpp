#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

#include "lob/analytics.hpp"
#include "lob/pipeline.hpp"

namespace {

struct Config {
    std::string data_file;
    std::string map_output_file;
    std::string flat_output_file;
    std::string summary_output_file;
    std::string ticker_override;
};

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

void append_csv_row(
    const std::string& filepath,
    const std::string& header,
    const std::string& row) {
    const bool needs_header =
        !std::filesystem::exists(filepath) || std::filesystem::file_size(filepath) == 0;
    std::ofstream output(filepath, std::ios::app);
    if (!output.is_open()) {
        throw std::runtime_error("could not open correctness output file");
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
        if (arg == "--map-output" && i + 1 < argc) {
            config.map_output_file = argv[++i];
            continue;
        }
        if (arg == "--flat-output" && i + 1 < argc) {
            config.flat_output_file = argv[++i];
            continue;
        }
        if (arg == "--summary-output" && i + 1 < argc) {
            config.summary_output_file = argv[++i];
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

    return config;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const Config config = parse_args(argc, argv);
        const std::string ticker = detect_ticker(config.data_file, config.ticker_override);

        const lob::ParsedMessages parsed = lob::load_messages(config.data_file);

        lob::PipelineOptions map_options{};
        map_options.container_type = lob::ContainerType::StdMap;
        map_options.store_analytics_rows = true;

        lob::PipelineOptions flat_options{};
        flat_options.container_type = lob::ContainerType::FlatVector;
        flat_options.store_analytics_rows = true;

        const lob::PipelineResult map_result = lob::run_pipeline_messages(parsed.messages, map_options);
        const lob::PipelineResult flat_result = lob::run_pipeline_messages(parsed.messages, flat_options);

        if (!config.map_output_file.empty() &&
            !lob::write_analytics_csv_file(config.map_output_file, map_result.analytics_rows)) {
            std::cerr << "Could not write map analytics CSV\n";
            return 1;
        }
        if (!config.flat_output_file.empty() &&
            !lob::write_analytics_csv_file(config.flat_output_file, flat_result.analytics_rows)) {
            std::cerr << "Could not write flat analytics CSV\n";
            return 1;
        }

        bool identical = map_result.analytics_rows.size() == flat_result.analytics_rows.size();
        std::size_t mismatch_index = 0;
        for (; identical && mismatch_index < map_result.analytics_rows.size(); ++mismatch_index) {
            if (!lob::analytics_rows_equal(
                    map_result.analytics_rows[mismatch_index],
                    flat_result.analytics_rows[mismatch_index])) {
                identical = false;
                break;
            }
        }

        if (!identical) {
            std::cerr << "Mismatch for " << ticker << " at analytics row " << mismatch_index << '\n';
        } else {
            std::cout << "IDENTICAL " << ticker << " messages=" << parsed.messages.size() << '\n';
        }

        if (!config.summary_output_file.empty()) {
            const std::string header = "ticker,data_file,messages,malformed,identical";
            std::ostringstream row;
            row << ticker << ','
                << config.data_file << ','
                << parsed.messages.size() << ','
                << parsed.malformed_messages << ','
                << (identical ? "true" : "false");
            append_csv_row(config.summary_output_file, header, row.str());
        }

        return identical ? 0 : 2;
    } catch (const std::exception& ex) {
        std::cerr << "Usage: verify_correctness --data <file> "
                     "[--map-output path] [--flat-output path] [--summary-output path] [--ticker NAME]\n";
        if (std::string(ex.what()).empty()) {
            return 1;
        }
        std::cerr << ex.what() << '\n';
        return 1;
    }
}
