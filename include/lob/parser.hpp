#pragma once

#include <cstddef>
#include <fstream>
#include <string>
#include <vector>

#include "lob/types.hpp"

namespace lob {

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
