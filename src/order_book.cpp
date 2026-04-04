#include "lob/order_book.hpp"

#include <algorithm>
#include <functional>
#include <map>
#include <unordered_map>
#include <utility>

namespace lob {
namespace {

struct LevelState {
    AggregateQuantity total_size{0};
    OrderCount order_count{0};
};

template <Side SideValue>
struct SideTraits {
    static constexpr Side side = SideValue;

    static constexpr bool price_before(Price lhs, Price rhs) noexcept {
        if constexpr (SideValue == Side::Buy) {
            return lhs > rhs;
        }
        return lhs < rhs;
    }

    struct MapComparator {
        bool operator()(Price lhs, Price rhs) const noexcept {
            return SideTraits::price_before(lhs, rhs);
        }
    };
};

template <typename Traits>
OrderBookLevel make_level(Price price, const LevelState& level_state) {
    return OrderBookLevel{price, level_state.total_size, level_state.order_count, Traits::side};
}

template <typename Traits>
class MapPriceLevels {
public:
    LevelState& find_or_create(Price price) {
        auto [it, inserted] = levels_.try_emplace(price, LevelState{});
        static_cast<void>(inserted);
        return it->second;
    }

    LevelState* find(Price price) {
        auto it = levels_.find(price);
        return it == levels_.end() ? nullptr : &it->second;
    }

    const LevelState* find(Price price) const {
        auto it = levels_.find(price);
        return it == levels_.end() ? nullptr : &it->second;
    }

    void erase(Price price) {
        levels_.erase(price);
    }

    std::optional<OrderBookLevel> level(Price price) const {
        const LevelState* found = find(price);
        if (found == nullptr) {
            return std::nullopt;
        }
        return make_level<Traits>(price, *found);
    }

    std::optional<OrderBookLevel> best() const {
        if (levels_.empty()) {
            return std::nullopt;
        }
        const auto& entry = *levels_.begin();
        return make_level<Traits>(entry.first, entry.second);
    }

    std::vector<OrderBookLevel> top_n(std::size_t depth) const {
        std::vector<OrderBookLevel> result;
        result.reserve(std::min(depth, levels_.size()));

        std::size_t index = 0;
        for (const auto& entry : levels_) {
            if (index++ >= depth) {
                break;
            }
            result.push_back(make_level<Traits>(entry.first, entry.second));
        }
        return result;
    }

private:
    std::map<Price, LevelState, typename Traits::MapComparator> levels_;
};

template <typename Traits>
class FlatPriceLevels {
public:
    LevelState& find_or_create(Price price) {
        auto it = lower_bound(price);
        if (it == levels_.end() || it->first != price) {
            it = levels_.insert(it, Entry{price, LevelState{}});
        }
        return it->second;
    }

    LevelState* find(Price price) {
        auto it = lower_bound(price);
        return (it == levels_.end() || it->first != price) ? nullptr : &it->second;
    }

    const LevelState* find(Price price) const {
        auto it = lower_bound(price);
        return (it == levels_.end() || it->first != price) ? nullptr : &it->second;
    }

    void erase(Price price) {
        auto it = lower_bound(price);
        if (it != levels_.end() && it->first == price) {
            levels_.erase(it);
        }
    }

    std::optional<OrderBookLevel> level(Price price) const {
        const LevelState* found = find(price);
        if (found == nullptr) {
            return std::nullopt;
        }
        return make_level<Traits>(price, *found);
    }

    std::optional<OrderBookLevel> best() const {
        if (levels_.empty()) {
            return std::nullopt;
        }
        return make_level<Traits>(levels_.front().first, levels_.front().second);
    }

    std::vector<OrderBookLevel> top_n(std::size_t depth) const {
        std::vector<OrderBookLevel> result;
        result.reserve(std::min(depth, levels_.size()));

        const std::size_t count = std::min(depth, levels_.size());
        for (std::size_t index = 0; index < count; ++index) {
            result.push_back(make_level<Traits>(levels_[index].first, levels_[index].second));
        }
        return result;
    }

private:
    using Entry = std::pair<Price, LevelState>;
    using Container = std::vector<Entry>;
    using Iterator = typename Container::iterator;
    using ConstIterator = typename Container::const_iterator;

    Iterator lower_bound(Price price) {
        return std::lower_bound(
            levels_.begin(),
            levels_.end(),
            price,
            [](const Entry& entry, Price value) { return Traits::price_before(entry.first, value); });
    }

    ConstIterator lower_bound(Price price) const {
        return std::lower_bound(
            levels_.begin(),
            levels_.end(),
            price,
            [](const Entry& entry, Price value) { return Traits::price_before(entry.first, value); });
    }

    Container levels_;
};

template <typename BidLevels, typename AskLevels>
class BasicOrderBookImpl {
public:
    void apply(const LobsterMessage& message) {
        switch (message.event_type) {
        case EventType::NewOrder:
            add_order(message.order_id, message.size, message.price, message.direction);
            break;
        case EventType::PartialCancel:
            reduce_order(message.order_id, message.size);
            break;
        case EventType::FullCancel:
            remove_order(message.order_id);
            break;
        case EventType::ExecutionVisible:
            reduce_order(message.order_id, message.size);
            break;
        case EventType::ExecutionHidden:
        case EventType::CrossTrade:
        case EventType::TradingHalt:
            break;
        }
    }

    std::optional<OrderBookLevel> best_bid() const {
        return bids_.best();
    }

    std::optional<OrderBookLevel> best_ask() const {
        return asks_.best();
    }

    std::optional<OrderBookLevel> level(Side side, Price price) const {
        return with_side(side, [price](const auto& levels) { return levels.level(price); });
    }

    std::vector<OrderBookLevel> levels(Side side, std::size_t depth) const {
        return with_side(side, [depth](const auto& levels) { return levels.top_n(depth); });
    }

    BookSnapshot snapshot(std::size_t depth) const {
        BookSnapshot result;
        result.best_bid = best_bid();
        result.best_ask = best_ask();
        result.bids = bids_.top_n(depth);
        result.asks = asks_.top_n(depth);
        result.active_order_count = orders_.size();

        if (result.best_bid.has_value() && result.best_ask.has_value()) {
            result.spread = result.best_ask->price - result.best_bid->price;
            result.mid_price =
                (static_cast<double>(result.best_bid->price) + static_cast<double>(result.best_ask->price)) / 2.0;
        }

        return result;
    }

    std::size_t active_order_count() const noexcept {
        return orders_.size();
    }

private:
    struct OrderState {
        Price price{0};
        Quantity remaining_size{0};
        Side side{Side::Buy};
    };

    void add_order(OrderId order_id, Quantity size, Price price, Side side) {
        if (size <= 0) {
            return;
        }

        auto existing = orders_.find(order_id);
        if (existing != orders_.end()) {
            erase_order(existing);
        }

        auto [it, inserted] = orders_.emplace(order_id, OrderState{price, size, side});
        static_cast<void>(inserted);

        LevelState& level_state = with_side(side, [price](auto& levels) -> LevelState& {
            return levels.find_or_create(price);
        });
        level_state.total_size += size;
        ++level_state.order_count;
    }

    void reduce_order(OrderId order_id, Quantity requested_reduction) {
        if (requested_reduction <= 0) {
            return;
        }

        auto it = orders_.find(order_id);
        if (it == orders_.end()) {
            return;
        }

        const Quantity reduction = std::min(it->second.remaining_size, requested_reduction);
        apply_reduction(it, reduction);
    }

    void remove_order(OrderId order_id) {
        auto it = orders_.find(order_id);
        if (it == orders_.end()) {
            return;
        }
        apply_reduction(it, it->second.remaining_size);
    }

    void erase_order(typename std::unordered_map<OrderId, OrderState>::iterator it) {
        if (it == orders_.end()) {
            return;
        }
        apply_reduction(it, it->second.remaining_size);
    }

    void apply_reduction(typename std::unordered_map<OrderId, OrderState>::iterator it, Quantity reduction) {
        if (reduction <= 0) {
            return;
        }

        OrderState& order = it->second;
        LevelState* level_state = with_side(order.side, [price = order.price](auto& levels) {
            return levels.find(price);
        });
        if (level_state == nullptr) {
            orders_.erase(it);
            return;
        }

        level_state->total_size -= reduction;

        if (reduction >= order.remaining_size) {
            if (level_state->order_count > 0) {
                --level_state->order_count;
            }
            with_side(order.side, [price = order.price](auto& levels) {
                LevelState* level = levels.find(price);
                if (level != nullptr && level->total_size == 0 && level->order_count == 0) {
                    levels.erase(price);
                }
            });
            orders_.erase(it);
            return;
        }

        order.remaining_size -= reduction;
        with_side(order.side, [price = order.price](auto& levels) {
            LevelState* level = levels.find(price);
            if (level != nullptr && level->total_size == 0 && level->order_count == 0) {
                levels.erase(price);
            }
        });
    }

    template <typename Fn>
    decltype(auto) with_side(Side side, Fn&& fn) {
        if (side == Side::Buy) {
            return fn(bids_);
        }
        return fn(asks_);
    }

    template <typename Fn>
    decltype(auto) with_side(Side side, Fn&& fn) const {
        if (side == Side::Buy) {
            return fn(bids_);
        }
        return fn(asks_);
    }

    BidLevels bids_;
    AskLevels asks_;
    std::unordered_map<OrderId, OrderState> orders_;
};

using BidMapLevels = MapPriceLevels<SideTraits<Side::Buy>>;
using AskMapLevels = MapPriceLevels<SideTraits<Side::Sell>>;
using BidFlatLevels = FlatPriceLevels<SideTraits<Side::Buy>>;
using AskFlatLevels = FlatPriceLevels<SideTraits<Side::Sell>>;

using MapOrderBookCore = BasicOrderBookImpl<BidMapLevels, AskMapLevels>;
using FlatVectorOrderBookCore = BasicOrderBookImpl<BidFlatLevels, AskFlatLevels>;

}  // namespace

bool operator==(const BookSnapshot& lhs, const BookSnapshot& rhs) noexcept {
    return lhs.best_bid == rhs.best_bid &&
           lhs.best_ask == rhs.best_ask &&
           lhs.bids == rhs.bids &&
           lhs.asks == rhs.asks &&
           lhs.active_order_count == rhs.active_order_count &&
           lhs.spread == rhs.spread &&
           lhs.mid_price == rhs.mid_price;
}

class MapOrderBook::Impl : public MapOrderBookCore {};

MapOrderBook::MapOrderBook() : impl_(std::make_unique<Impl>()) {}

MapOrderBook::~MapOrderBook() = default;
MapOrderBook::MapOrderBook(MapOrderBook&&) noexcept = default;
MapOrderBook& MapOrderBook::operator=(MapOrderBook&&) noexcept = default;

void MapOrderBook::apply(const LobsterMessage& message) {
    impl_->apply(message);
}

std::optional<OrderBookLevel> MapOrderBook::best_bid() const {
    return impl_->best_bid();
}

std::optional<OrderBookLevel> MapOrderBook::best_ask() const {
    return impl_->best_ask();
}

std::optional<OrderBookLevel> MapOrderBook::level(Side side, Price price) const {
    return impl_->level(side, price);
}

std::vector<OrderBookLevel> MapOrderBook::levels(Side side, std::size_t depth) const {
    return impl_->levels(side, depth);
}

BookSnapshot MapOrderBook::snapshot(std::size_t depth) const {
    return impl_->snapshot(depth);
}

std::size_t MapOrderBook::active_order_count() const noexcept {
    return impl_->active_order_count();
}

class FlatVectorOrderBook::Impl : public FlatVectorOrderBookCore {};

FlatVectorOrderBook::FlatVectorOrderBook() : impl_(std::make_unique<Impl>()) {}

FlatVectorOrderBook::~FlatVectorOrderBook() = default;
FlatVectorOrderBook::FlatVectorOrderBook(FlatVectorOrderBook&&) noexcept = default;
FlatVectorOrderBook& FlatVectorOrderBook::operator=(FlatVectorOrderBook&&) noexcept = default;

void FlatVectorOrderBook::apply(const LobsterMessage& message) {
    impl_->apply(message);
}

std::optional<OrderBookLevel> FlatVectorOrderBook::best_bid() const {
    return impl_->best_bid();
}

std::optional<OrderBookLevel> FlatVectorOrderBook::best_ask() const {
    return impl_->best_ask();
}

std::optional<OrderBookLevel> FlatVectorOrderBook::level(Side side, Price price) const {
    return impl_->level(side, price);
}

std::vector<OrderBookLevel> FlatVectorOrderBook::levels(Side side, std::size_t depth) const {
    return impl_->levels(side, depth);
}

BookSnapshot FlatVectorOrderBook::snapshot(std::size_t depth) const {
    return impl_->snapshot(depth);
}

std::size_t FlatVectorOrderBook::active_order_count() const noexcept {
    return impl_->active_order_count();
}

std::unique_ptr<OrderBook> make_order_book(OrderBookBackend backend) {
    switch (backend) {
    case OrderBookBackend::Map:
        return std::make_unique<MapOrderBook>();
    case OrderBookBackend::FlatVector:
        return std::make_unique<FlatVectorOrderBook>();
    }
    return nullptr;
}

const char* to_string(OrderBookBackend backend) noexcept {
    switch (backend) {
    case OrderBookBackend::Map:
        return "map";
    case OrderBookBackend::FlatVector:
        return "flat_vector";
    }
    return "unknown";
}

std::optional<OrderBookBackend> parse_order_book_backend(const std::string& value) {
    if (value == "map") {
        return OrderBookBackend::Map;
    }
    if (value == "flat" || value == "flat_vector" || value == "vector") {
        return OrderBookBackend::FlatVector;
    }
    return std::nullopt;
}

}  // namespace lob
