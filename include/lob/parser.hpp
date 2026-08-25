#pragma once

#include <cstddef>
#include <fstream>
#include <string>
#include <vector>

#include "lob/types.hpp"

namespace lob {

// Parse the FIRST row of a LOBSTER orderbook_N file (columns repeat
// ask_px, ask_sz, bid_px, bid_sz per level) into seedable levels.
// Empty-level sentinels (+/-9999999999, non-positive sizes) are skipped.
std::vector<SeedLevel> parse_orderbook_seed_row(const std::string& filepath);

class LobsterParser {
public:
    std::vector<LobsterMessage> parse_file(const std::string& filepath);

    bool open(const std::string& filepath);
    bool next(LobsterMessage& msg);

    std::size_t parsed_count() const noexcept;
    std::size_t malformed_count() const noexcept;

private:
    bool parse_line(const std::string& line, LobsterMessage& out);
    void reset();

    std::ifstream stream_;
    std::size_t parsed_count_{0};
    std::size_t malformed_count_{0};
};

}  // namespace lob
