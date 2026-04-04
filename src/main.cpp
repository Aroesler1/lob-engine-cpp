#include <cmath>
#include <iomanip>
#include <iostream>
#include <string>

#include "lob/analytics.hpp"
#include "lob/pipeline.hpp"

int main(int argc, char* argv[]) {
    if (argc < 2 || argc > 6) {
        std::cerr << "Usage: lob_engine <lobster_csv_file> [--container map|flat_vec] [--analytics-out path]\n";
        return 1;
    }

    std::string filepath;
    std::string analytics_out;
    lob::ContainerType container_type = lob::ContainerType::StdMap;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--container" && i + 1 < argc) {
            if (!lob::parse_container_type(argv[++i], container_type)) {
                std::cerr << "Unknown container type: " << argv[i] << '\n';
                return 1;
            }
            continue;
        }
        if (arg == "--analytics-out" && i + 1 < argc) {
            analytics_out = argv[++i];
            continue;
        }
        if (!filepath.empty()) {
            std::cerr << "Unexpected argument: " << arg << '\n';
            return 1;
        }
        filepath = arg;
    }

    if (filepath.empty()) {
        std::cerr << "Missing lobster CSV file path\n";
        return 1;
    }

    lob::PipelineOptions options{};
    options.container_type = container_type;
    options.store_analytics_rows = !analytics_out.empty();

    const lob::PipelineResult result = lob::run_pipeline_file(filepath, options);
    std::cout << "Parsed: " << result.parsed_messages << '\n';
    std::cout << "Malformed: " << result.malformed_messages << '\n';
    if (result.parsed_messages == 0) {
        std::cout << "Time range: n/a\n";
    } else {
        std::cout << std::fixed << std::setprecision(6);
        std::cout << "Time range: " << result.first_timestamp << " - " << result.last_timestamp << '\n';
    }

    std::cout << "Container: " << lob::container_type_name(container_type) << '\n';
    std::cout << "Peak active levels: " << result.peak_active_levels << '\n';
    std::cout << "Final active orders: " << result.final_total_orders << '\n';

    if (!analytics_out.empty()) {
        if (!lob::write_analytics_csv_file(analytics_out, result.analytics_rows)) {
            std::cerr << "Could not write analytics CSV: " << analytics_out << '\n';
            return 1;
        }
        std::cout << "Analytics CSV: " << analytics_out << '\n';
    }

    return 0;
}
