#define CATCH_CONFIG_MAIN
#include <catch2/catch.hpp>

#include "lobster_parser.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <string_view>

namespace {

struct TempCsvFile {
    explicit TempCsvFile(std::string_view contents) {
        static int counter = 0;
        path = std::filesystem::temp_directory_path() /
            ("lobster_parser_test_" + std::to_string(counter++) + ".csv");

        std::ofstream output(path);
        REQUIRE(output.is_open());
        output << contents;
    }

    ~TempCsvFile() {
        std::error_code error;
        std::filesystem::remove(path, error);
    }

    std::filesystem::path path;
};

void requireMessageEquals(const LobsterMessage& actual, const LobsterMessage& expected) {
    REQUIRE(actual.timestamp == Approx(expected.timestamp));
    REQUIRE(actual.event_type == expected.event_type);
    REQUIRE(actual.order_id == expected.order_id);
    REQUIRE(actual.size == expected.size);
    REQUIRE(actual.price == expected.price);
    REQUIRE(actual.direction == expected.direction);
}

}  // namespace

TEST_CASE("parseLobsterLine converts representative rows into typed LobsterMessage values", "[parser]") {
    SECTION("visible execution rows preserve timestamp id size price and sell direction") {
        const std::string line = "34200.123456,4,123456789,250,1254300,-1";
        const LobsterMessage expected{34200.123456, 4, 123456789, 250, 1254300, -1};

        LobsterMessage first{};
        LobsterMessage second{};

        REQUIRE(parseLobsterLine(line, first));
        REQUIRE(parseLobsterLine(line, second));
        requireMessageEquals(first, expected);
        requireMessageEquals(second, expected);
    }

    SECTION("trading status rows keep zero sized control fields typed as integers") {
        const std::string line = "34200.500000,7,0,0,-1,0";
        const LobsterMessage expected{34200.5, 7, 0, 0, -1, 0};

        LobsterMessage parsed{};
        REQUIRE(parseLobsterLine(line, parsed));
        requireMessageEquals(parsed, expected);
    }
}

TEST_CASE("parseLobsterLine rejects malformed and non-data rows deterministically", "[parser]") {
    LobsterMessage parsed{123.0, 9, 9, 9, 9, 9};

    REQUIRE_FALSE(parseLobsterLine("timestamp,event_type,order_id,size,price,direction", parsed));
    REQUIRE_FALSE(parseLobsterLine("34200.123456,1,123,10,1000000", parsed));
    REQUIRE_FALSE(parseLobsterLine("34200.123456,1,abc,10,1000000,1", parsed));
}

TEST_CASE("parseLobsterCsv throws when a malformed non-header row is encountered", "[parser]") {
    std::istringstream input(
        "timestamp,event_type,order_id,size,price,direction\n"
        "34200.000001,1,1001,10,1000000,1\n"
        "34200.000002,1,broken,10,1002000,-1\n");

    REQUIRE_THROWS(parseLobsterCsv(input));
}

TEST_CASE("istream and file parsing produce identical typed messages across repeated parses", "[parser]") {
    const std::string csv_contents =
        "timestamp,event_type,order_id,size,price,direction\r\n"
        "34200.000001,1,1001,10,1000000,1\r\n"
        "34200.000002,4,2001,3,1002000,-1\r\n"
        "34200.000003,7,0,0,1,0\r\n";

    std::istringstream stream_a(csv_contents);
    std::istringstream stream_b(csv_contents);
    const auto parsed_from_stream_a = parseLobsterCsv(stream_a);
    const auto parsed_from_stream_b = parseLobsterCsv(stream_b);

    const TempCsvFile csv_file(csv_contents);
    const auto parsed_from_file = parseLobsterCsvFile(csv_file.path);

    REQUIRE(parsed_from_stream_a.size() == 3);
    REQUIRE(parsed_from_stream_b.size() == parsed_from_stream_a.size());
    REQUIRE(parsed_from_file.size() == parsed_from_stream_a.size());

    for (std::size_t index = 0; index < parsed_from_stream_a.size(); ++index) {
        requireMessageEquals(parsed_from_stream_a[index], parsed_from_stream_b[index]);
        requireMessageEquals(parsed_from_stream_a[index], parsed_from_file[index]);
    }
}
