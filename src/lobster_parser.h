#pragma once

#include <cstdint>
#include <filesystem>
#include <istream>
#include <string_view>
#include <vector>

struct LobsterMessage {
    double timestamp = 0.0;
    int event_type = 0;
    int64_t order_id = 0;
    int64_t size = 0;
    int64_t price = 0;
    int direction = 0;
};

bool parseLobsterLine(std::string_view line, LobsterMessage& out_message);
std::vector<LobsterMessage> parseLobsterCsv(std::istream& input);
std::vector<LobsterMessage> parseLobsterCsvFile(const std::filesystem::path& path);
