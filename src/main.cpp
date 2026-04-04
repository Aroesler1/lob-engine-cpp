#include <array>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

#include "lob/parser.hpp"

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "Usage: lob_engine <lobster_csv_file>\n";
        return 1;
    }

    const std::string filepath = argv[1];
    std::ifstream probe(filepath);
    if (!probe.is_open()) {
        std::cerr << "Could not open file: " << filepath << '\n';
        return 1;
    }

    lob::LobsterParser parser;
    const std::vector<lob::LobsterMessage> messages = parser.parse_file(filepath);

    std::array<std::size_t, 7> event_counts{};
    for (const lob::LobsterMessage& message : messages) {
        const std::size_t index = static_cast<std::size_t>(static_cast<int>(message.event_type) - 1);
        ++event_counts[index];
    }

    std::cout << "Parsed: " << messages.size() << '\n';
    std::cout << "Malformed: " << parser.malformed_count() << '\n';
    if (messages.empty()) {
        std::cout << "Time range: n/a\n";
    } else {
        std::cout << std::fixed << std::setprecision(6);
        std::cout << "Time range: " << messages.front().timestamp << " - " << messages.back().timestamp << '\n';
    }

    std::cout << "Event type counts:";
    for (std::size_t i = 0; i < event_counts.size(); ++i) {
        std::cout << ' ' << (i + 1) << ':' << event_counts[i];
    }
    std::cout << '\n';

    return 0;
}
