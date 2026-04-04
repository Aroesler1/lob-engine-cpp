#include "lob/parser.hpp"

#include <cctype>
#include <cstdint>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace lob {
namespace {

std::string trim_copy(const std::string& input) {
    std::size_t start = 0;
    while (start < input.size() && std::isspace(static_cast<unsigned char>(input[start])) != 0) {
        ++start;
    }

    std::size_t end = input.size();
    while (end > start && std::isspace(static_cast<unsigned char>(input[end - 1])) != 0) {
        --end;
    }

    return input.substr(start, end - start);
}

template <typename Integer>
Integer parse_integer(const std::string& input) {
    std::size_t consumed = 0;
    long long value = std::stoll(trim_copy(input), &consumed);
    const std::string trimmed = trim_copy(input);
    if (consumed != trimmed.size()) {
        throw std::invalid_argument("unexpected integer suffix");
    }
    if (value < static_cast<long long>(std::numeric_limits<Integer>::min()) ||
        value > static_cast<long long>(std::numeric_limits<Integer>::max())) {
        throw std::out_of_range("integer outside target range");
    }
    return static_cast<Integer>(value);
}

double parse_double(const std::string& input) {
    const std::string trimmed = trim_copy(input);
    std::size_t consumed = 0;
    double value = std::stod(trimmed, &consumed);
    if (consumed != trimmed.size()) {
        throw std::invalid_argument("unexpected floating-point suffix");
    }
    return value;
}

std::vector<std::string> split_csv_fields(const std::string& line) {
    std::vector<std::string> fields;
    std::stringstream buffer(line);
    std::string field;
    while (std::getline(buffer, field, ',')) {
        fields.push_back(field);
    }
    if (!line.empty() && line.back() == ',') {
        fields.emplace_back();
    }
    return fields;
}

}  // namespace

std::vector<LobsterMessage> LobsterParser::parse_file(const std::string& filepath) {
    std::vector<LobsterMessage> messages;
    if (!open(filepath)) {
        return messages;
    }

    LobsterMessage message{};
    while (next(message)) {
        messages.push_back(message);
    }

    return messages;
}

bool LobsterParser::open(const std::string& filepath) {
    reset();
    stream_.close();
    stream_.clear();
    stream_.open(filepath);
    return stream_.is_open();
}

bool LobsterParser::next(LobsterMessage& msg) {
    if (!stream_.is_open()) {
        return false;
    }

    std::string line;
    while (std::getline(stream_, line)) {
        if (parse_line(line, msg)) {
            ++parsed_count_;
            return true;
        }
        ++malformed_count_;
    }

    return false;
}

std::size_t LobsterParser::parsed_count() const noexcept {
    return parsed_count_;
}

std::size_t LobsterParser::malformed_count() const noexcept {
    return malformed_count_;
}

bool LobsterParser::parse_line(const std::string& line, LobsterMessage& out) {
    try {
        const std::vector<std::string> fields = split_csv_fields(line);
        if (fields.size() != 6) {
            return false;
        }

        out.timestamp = parse_double(fields[0]);
        out.event_type = int_to_event_type(parse_integer<int>(fields[1]));
        out.order_id = parse_integer<std::int64_t>(fields[2]);
        out.size = parse_integer<std::int32_t>(fields[3]);
        out.price = parse_integer<std::int64_t>(fields[4]);
        out.direction = direction_to_side(parse_integer<int>(fields[5]));
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

void LobsterParser::reset() {
    parsed_count_ = 0;
    malformed_count_ = 0;
}

}  // namespace lob
