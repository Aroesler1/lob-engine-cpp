#include "lobster_parser.h"

#include <cctype>
#include <fstream>
#include <stdexcept>
#include <string>

namespace {

bool isHeaderLine(std::string_view line) {
    if (line.empty()) {
        return false;
    }

    const unsigned char first = static_cast<unsigned char>(line.front());
    return !std::isdigit(first) && line.front() != '-' && line.front() != '.';
}

std::string_view trimLine(std::string_view line) {
    if (!line.empty() && line.back() == '\r') {
        line.remove_suffix(1);
    }
    return line;
}

}  // namespace

bool parseLobsterLine(std::string_view line, LobsterMessage& out_message) {
    line = trimLine(line);
    if (line.empty() || isHeaderLine(line)) {
        return false;
    }

    std::string_view fields[6];
    std::size_t field_count = 0;
    std::size_t start = 0;

    while (start <= line.size() && field_count < 6) {
        const std::size_t end = line.find(',', start);
        if (end == std::string_view::npos) {
            fields[field_count++] = line.substr(start);
            break;
        }

        fields[field_count++] = line.substr(start, end - start);
        start = end + 1;
    }

    if (field_count != 6 || line.find(',', start) != std::string_view::npos) {
        return false;
    }

    try {
        out_message.timestamp = std::stod(std::string(fields[0]));
        out_message.event_type = std::stoi(std::string(fields[1]));
        out_message.order_id = std::stoll(std::string(fields[2]));
        out_message.size = std::stoll(std::string(fields[3]));
        out_message.price = std::stoll(std::string(fields[4]));
        out_message.direction = std::stoi(std::string(fields[5]));
    } catch (const std::exception&) {
        return false;
    }

    return true;
}

std::vector<LobsterMessage> parseLobsterCsv(std::istream& input) {
    std::vector<LobsterMessage> messages;
    std::string line;
    std::size_t line_number = 0;

    while (std::getline(input, line)) {
        ++line_number;

        LobsterMessage message;
        if (parseLobsterLine(line, message)) {
            messages.push_back(message);
            continue;
        }

        const std::string_view trimmed = trimLine(line);
        if (!trimmed.empty() && !isHeaderLine(trimmed)) {
            throw std::runtime_error("Failed to parse CSV line " + std::to_string(line_number));
        }
    }

    return messages;
}

std::vector<LobsterMessage> parseLobsterCsvFile(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input.is_open()) {
        throw std::runtime_error("Unable to open CSV file: " + path.string());
    }

    return parseLobsterCsv(input);
}
