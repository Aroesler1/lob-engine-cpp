#include "lob/replay.hpp"

#include <chrono>
#include <utility>

namespace lob {

void replay_messages(const std::vector<LobsterMessage>& messages, OrderBook& book) {
    for (const LobsterMessage& message : messages) {
        book.apply(message);
    }
}

ReplaySummary benchmark_replay(
    const std::vector<LobsterMessage>& messages,
    OrderBookBackend backend,
    std::size_t depth,
    std::size_t repeats,
    OrderBookBuildConfig config) {
    const std::size_t safe_repeats = repeats == 0 ? 1 : repeats;
    BookSnapshot final_snapshot;

    const auto start = std::chrono::steady_clock::now();
    for (std::size_t iteration = 0; iteration < safe_repeats; ++iteration) {
        std::unique_ptr<OrderBook> book = make_order_book(backend, config);
        replay_messages(messages, *book);
        if (iteration + 1 == safe_repeats) {
            final_snapshot = book->snapshot(depth);
        }
    }
    const auto end = std::chrono::steady_clock::now();

    const double elapsed_ms = std::chrono::duration<double, std::milli>(end - start).count();
    const double elapsed_seconds = elapsed_ms / 1000.0;
    const std::size_t processed_messages = messages.size() * safe_repeats;

    ReplaySummary summary;
    summary.backend = backend;
    summary.processed_messages = processed_messages;
    summary.repeats = safe_repeats;
    summary.elapsed_ms = elapsed_ms;
    summary.messages_per_second = elapsed_seconds > 0.0 ? processed_messages / elapsed_seconds : 0.0;
    summary.final_snapshot = std::move(final_snapshot);
    return summary;
}

}  // namespace lob
