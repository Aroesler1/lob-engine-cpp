#include "analytics_engine.h"
#include "csv_exporter.h"
#include "lobster_parser.h"
#include "order_book.h"

#include <filesystem>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>

namespace {

struct Options {
    int top = 1;
    std::string analytics_out;
    std::string input_path;
};

void printUsage(const char* program_name) {
    std::cerr << "Usage: " << program_name << " [--top N] [--analytics-out PATH] <lobster_csv>\n";
}

std::optional<Options> parseArgs(int argc, char** argv) {
    Options options;

    try {
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--top") {
                if (i + 1 >= argc) {
                    return std::nullopt;
                }

                options.top = std::stoi(argv[++i]);
                if (options.top <= 0) {
                    return std::nullopt;
                }
                continue;
            }

            if (arg == "--analytics-out") {
                if (i + 1 >= argc) {
                    return std::nullopt;
                }
                options.analytics_out = argv[++i];
                continue;
            }

            if (arg == "-h" || arg == "--help") {
                return std::nullopt;
            }

            if (!options.input_path.empty()) {
                return std::nullopt;
            }
            options.input_path = arg;
        }
    } catch (const std::exception&) {
        return std::nullopt;
    }

    if (options.input_path.empty()) {
        return std::nullopt;
    }

    return options;
}

template <typename LevelMap>
std::string formatLevels(const LevelMap& levels, int top_n) {
    if (levels.empty()) {
        return "---";
    }

    std::ostringstream output;
    int emitted = 0;
    for (const auto& [price, level] : levels) {
        if (emitted > 0) {
            output << ", ";
        }
        output << price << 'x' << level.total_size;
        ++emitted;
        if (emitted >= top_n) {
            break;
        }
    }
    return output.str();
}

std::string formatSpread(const OrderBook& book) {
    const auto best_bid = book.bestBid();
    const auto best_ask = book.bestAsk();
    if (!best_bid || !best_ask) {
        return "NA";
    }
    return std::to_string(*best_ask - *best_bid);
}

std::string formatTimestamp(double timestamp) {
    std::ostringstream output;
    output << std::fixed << std::setprecision(6) << timestamp;
    return output.str();
}

std::filesystem::path defaultAnalyticsOutputPath(const std::string& input_path) {
    const std::filesystem::path input(input_path);
    const std::string stem = input.stem().empty() ? std::string("analytics") : input.stem().string();
    return std::filesystem::current_path() / (stem + "_analytics.csv");
}

void printBookLine(const LobsterMessage& msg, const OrderBook& book, int top_n) {
    if (top_n == 1) {
        const std::string best_bid = book.bids().empty()
            ? "---"
            : std::to_string(book.bids().begin()->first) + "x" + std::to_string(book.bids().begin()->second.total_size);
        const std::string best_ask = book.asks().empty()
            ? "---"
            : std::to_string(book.asks().begin()->first) + "x" + std::to_string(book.asks().begin()->second.total_size);

        std::cout << formatTimestamp(msg.timestamp)
                  << " BBO: " << best_bid
                  << " | " << best_ask
                  << "  spread=" << formatSpread(book) << '\n';
        return;
    }

    std::cout << formatTimestamp(msg.timestamp)
              << " TOP " << top_n
              << ": bids=[" << formatLevels(book.bids(), top_n)
              << "] asks=[" << formatLevels(book.asks(), top_n)
              << "] spread=" << formatSpread(book) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    const auto options = parseArgs(argc, argv);
    if (!options) {
        printUsage(argv[0]);
        return 1;
    }

    try {
        const auto messages = parseLobsterCsvFile(options->input_path);
        OrderBook book;
        AnalyticsEngine analytics_engine;
        const std::filesystem::path analytics_path = options->analytics_out.empty()
            ? defaultAnalyticsOutputPath(options->input_path)
            : std::filesystem::path(options->analytics_out);
        CsvExporter exporter(analytics_path);

        for (const auto& message : messages) {
            book.processMessage(message);
            exporter.write(analytics_engine.onMessage(message, book));
            printBookLine(message, book, options->top);
        }
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "ERROR: " << ex.what() << '\n';
        return 1;
    }
}
