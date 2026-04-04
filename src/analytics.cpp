#include "lob/analytics.hpp"

#include <cmath>
#include <fstream>
#include <iomanip>

namespace lob {

namespace {

bool doubles_equal(double lhs, double rhs, double epsilon) {
    if (std::isnan(lhs) && std::isnan(rhs)) {
        return true;
    }
    return std::fabs(lhs - rhs) <= epsilon;
}

}  // namespace

void write_analytics_csv(std::ostream& output, const std::vector<AnalyticsRow>& rows) {
    output << "sequence,timestamp,event_type,order_id,message_size,message_price,direction,"
              "best_bid,best_ask,spread,mid_price,total_bid_size,total_ask_size,total_orders,"
              "bid_levels,ask_levels,active_levels\n";
    output << std::fixed << std::setprecision(6);

    for (const AnalyticsRow& row : rows) {
        output << row.sequence << ','
               << row.timestamp << ','
               << row.event_type << ','
               << row.order_id << ','
               << row.message_size << ','
               << row.message_price << ','
               << row.direction << ','
               << row.best_bid << ','
               << row.best_ask << ','
               << row.spread << ',';

        if (std::isnan(row.mid_price)) {
            output << "nan";
        } else {
            output << std::setprecision(4) << row.mid_price << std::setprecision(6);
        }

        output << ','
               << row.total_bid_size << ','
               << row.total_ask_size << ','
               << row.total_orders << ','
               << row.bid_levels << ','
               << row.ask_levels << ','
               << row.active_levels << '\n';
    }
}

bool write_analytics_csv_file(const std::string& filepath, const std::vector<AnalyticsRow>& rows) {
    std::ofstream output(filepath);
    if (!output.is_open()) {
        return false;
    }

    write_analytics_csv(output, rows);
    return true;
}

bool analytics_rows_equal(const AnalyticsRow& lhs, const AnalyticsRow& rhs, double epsilon) {
    return lhs.sequence == rhs.sequence &&
           doubles_equal(lhs.timestamp, rhs.timestamp, epsilon) &&
           lhs.event_type == rhs.event_type &&
           lhs.order_id == rhs.order_id &&
           lhs.message_size == rhs.message_size &&
           lhs.message_price == rhs.message_price &&
           lhs.direction == rhs.direction &&
           lhs.best_bid == rhs.best_bid &&
           lhs.best_ask == rhs.best_ask &&
           lhs.spread == rhs.spread &&
           doubles_equal(lhs.mid_price, rhs.mid_price, epsilon) &&
           lhs.total_bid_size == rhs.total_bid_size &&
           lhs.total_ask_size == rhs.total_ask_size &&
           lhs.total_orders == rhs.total_orders &&
           lhs.bid_levels == rhs.bid_levels &&
           lhs.ask_levels == rhs.ask_levels &&
           lhs.active_levels == rhs.active_levels;
}

}  // namespace lob
