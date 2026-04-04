#pragma once

#include <cstddef>
#include <vector>

#include "lob/order_book.hpp"
#include "lob/types.hpp"

namespace lob {

struct ReplaySummary {
    OrderBookBackend backend;
    std::size_t processed_messages{0};
    std::size_t repeats{0};
    double elapsed_ms{0.0};
    double messages_per_second{0.0};
    BookSnapshot final_snapshot;
};

void replay_messages(const std::vector<LobsterMessage>& messages, OrderBook& book);

ReplaySummary benchmark_replay(
    const std::vector<LobsterMessage>& messages,
    OrderBookBackend backend,
    std::size_t depth,
    std::size_t repeats);

}  // namespace lob
