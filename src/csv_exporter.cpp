#include "csv_exporter.h"

#include <cmath>
#include <filesystem>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {

std::string formatFixed(double value, int precision) {
    if (std::isnan(value)) {
        return "";
    }

    std::ostringstream output;
    output << std::fixed << std::setprecision(precision) << value;
    return output.str();
}

}  // namespace

CsvExporter::CsvExporter(const std::filesystem::path& path)
    : path_(path) {
    if (path_.has_parent_path()) {
        std::filesystem::create_directories(path_.parent_path());
    }

    output_.open(path_);
    if (!output_.is_open()) {
        throw std::runtime_error("Unable to open analytics CSV file: " + path_.string());
    }

    writeHeader();
}

void CsvExporter::write(const AnalyticsRecord& record) {
    output_ << formatFixed(record.timestamp, 6) << ','
            << formatFixed(record.best_bid, 4) << ','
            << formatFixed(record.best_ask, 4) << ','
            << formatFixed(record.spread, 4) << ','
            << formatFixed(record.mid, 4) << ','
            << record.bid_depth_1 << ','
            << record.ask_depth_1 << ','
            << record.bid_depth_5 << ','
            << record.ask_depth_5 << ','
            << record.bid_depth_10 << ','
            << record.ask_depth_10 << ','
            << formatFixed(record.order_imbalance, 6) << ','
            << formatFixed(record.rolling_vwap, 4) << ','
            << formatFixed(record.trade_flow_imbalance, 6) << ','
            << formatFixed(record.rolling_realized_vol, 6) << '\n';
}

const std::filesystem::path& CsvExporter::path() const {
    return path_;
}

void CsvExporter::writeHeader() {
    output_ << "timestamp,"
            << "best_bid,"
            << "best_ask,"
            << "spread,"
            << "mid,"
            << "bid_depth_1,"
            << "ask_depth_1,"
            << "bid_depth_5,"
            << "ask_depth_5,"
            << "bid_depth_10,"
            << "ask_depth_10,"
            << "order_imbalance,"
            << "rolling_vwap,"
            << "trade_flow_imbalance,"
            << "rolling_realized_vol\n";
}
