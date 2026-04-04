#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "lob/parser.hpp"
#include "lob/types.hpp"

namespace {

std::filesystem::path source_root() {
    return std::filesystem::path(LOB_ENGINE_SOURCE_DIR);
}

std::filesystem::path make_temp_file(const std::string& stem) {
    static int counter = 0;
    return std::filesystem::temp_directory_path() /
           (stem + "_" + std::to_string(counter++) + ".csv");
}

void write_file(const std::filesystem::path& path, const std::string& contents) {
    std::ofstream output(path);
    assert(output.is_open());
    output << contents;
}

void test_parse_known_valid_line() {
    const auto path = make_temp_file("known_valid");
    write_file(path, "34200.123456,1,123456789,250,1254300,1\n");

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(path.string());

    assert(messages.size() == 1);
    assert(parser.parsed_count() == 1);
    assert(parser.malformed_count() == 0);
    assert(messages[0].timestamp == 34200.123456);
    assert(messages[0].event_type == lob::EventType::NewOrder);
    assert(messages[0].order_id == 123456789);
    assert(messages[0].size == 250);
    assert(messages[0].price == 1254300);
    assert(messages[0].direction == lob::Side::Buy);

    std::filesystem::remove(path);
}

void test_sample_file_counts() {
    const auto sample_path = source_root() / "data" / "sample_messages.csv";
    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(sample_path.string());

    assert(messages.size() == 20);
    assert(parser.parsed_count() == 20);
    assert(parser.malformed_count() == 5);
    assert(messages.front().event_type == lob::EventType::NewOrder);
    assert(messages.back().event_type == lob::EventType::CrossTrade);
}

void test_malformed_lines_are_counted() {
    const auto path = make_temp_file("malformed_mix");
    write_file(
        path,
        "34200.100000,1,111,50,1254300,1\n"
        "34200.200000,1,111,50,1254300\n"
        "34200.300000,1,abc,50,1254300,1\n");

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(path.string());

    assert(messages.size() == 1);
    assert(parser.parsed_count() == 1);
    assert(parser.malformed_count() == 2);

    std::filesystem::remove(path);
}

void test_streaming_matches_batch() {
    const auto sample_path = source_root() / "data" / "sample_messages.csv";

    lob::LobsterParser batch_parser;
    const std::vector<lob::LobsterMessage> batch_messages = batch_parser.parse_file(sample_path.string());

    lob::LobsterParser streaming_parser;
    assert(streaming_parser.open(sample_path.string()));

    lob::LobsterMessage message{};
    std::size_t stream_count = 0;
    while (streaming_parser.next(message)) {
        ++stream_count;
    }

    assert(stream_count == batch_messages.size());
    assert(streaming_parser.parsed_count() == batch_parser.parsed_count());
    assert(streaming_parser.malformed_count() == batch_parser.malformed_count());
}

void test_empty_file_has_zero_counts() {
    const auto path = make_temp_file("empty");
    write_file(path, "");

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(path.string());

    assert(messages.empty());
    assert(parser.parsed_count() == 0);
    assert(parser.malformed_count() == 0);

    std::filesystem::remove(path);
}

void test_invalid_direction_is_malformed() {
    const auto path = make_temp_file("invalid_direction");
    write_file(path, "34200.100000,1,111,50,1254300,0\n");

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(path.string());

    assert(messages.empty());
    assert(parser.parsed_count() == 0);
    assert(parser.malformed_count() == 1);

    std::filesystem::remove(path);
}

void test_invalid_event_types_are_malformed() {
    const auto path = make_temp_file("invalid_event_type");
    write_file(
        path,
        "34200.100000,0,111,50,1254300,1\n"
        "34200.200000,8,112,50,1254300,-1\n"
        "34200.300000,4,113,50,1254300,1\n");

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(path.string());

    assert(messages.size() == 1);
    assert(parser.parsed_count() == 1);
    assert(parser.malformed_count() == 2);
    assert(messages[0].event_type == lob::EventType::ExecutionVisible);

    std::filesystem::remove(path);
}

}  // namespace

int main() {
    test_parse_known_valid_line();
    test_sample_file_counts();
    test_malformed_lines_are_counted();
    test_streaming_matches_batch();
    test_empty_file_has_zero_counts();
    test_invalid_direction_is_malformed();
    test_invalid_event_types_are_malformed();

    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
