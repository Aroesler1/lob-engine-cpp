#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "lob/parser.hpp"

namespace {

std::size_t parse_iterations(const char* value) {
    try {
        const std::size_t iterations = static_cast<std::size_t>(std::stoull(value));
        if (iterations == 0) {
            throw std::invalid_argument("iteration count must be positive");
        }
        return iterations;
    } catch (const std::exception&) {
        throw std::invalid_argument("iterations must be a positive integer");
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 2 || argc > 3) {
        std::cerr << "Usage: lob_benchmark <lobster_csv_file> [iterations]\n";
        return 1;
    }

    const std::string filepath = argv[1];
    const std::size_t iterations = argc == 3 ? parse_iterations(argv[2]) : 10;

    std::ifstream probe(filepath);
    if (!probe.is_open()) {
        std::cerr << "Could not open file: " << filepath << '\n';
        return 1;
    }

    std::size_t parsed_messages = 0;
    std::size_t malformed_messages = 0;

    const auto start = std::chrono::steady_clock::now();
    for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
        lob::LobsterParser parser;
        const std::vector<lob::LobsterMessage> messages = parser.parse_file(filepath);

        parsed_messages = messages.size();
        malformed_messages = parser.malformed_count();
    }
    const auto end = std::chrono::steady_clock::now();

    const std::chrono::duration<double, std::milli> elapsed = end - start;
    const double average_ms = elapsed.count() / static_cast<double>(iterations);

    std::cout << std::fixed << std::setprecision(3);
    std::cout << "Benchmark results\n";
    std::cout << "File: " << filepath << '\n';
    std::cout << "Iterations: " << iterations << '\n';
    std::cout << "Parsed messages: " << parsed_messages << '\n';
    std::cout << "Malformed messages: " << malformed_messages << '\n';
    std::cout << "Total elapsed ms: " << elapsed.count() << '\n';
    std::cout << "Average elapsed ms: " << average_ms << '\n';

    return 0;
}
