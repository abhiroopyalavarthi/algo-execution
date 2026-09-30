#include "BarExchange.h"

#include <algorithm>

BarExchange::BarExchange(Price minPrice, Price maxPrice, int capBps,
                         int halfSpreadTicks, int depthBps)
    : book_(minPrice, maxPrice, 1 << 12),
      minPrice_(minPrice),
      maxPrice_(maxPrice),
      capBps_(capBps),
      halfSpread_(halfSpreadTicks),
      depthBps_(depthBps) {
    trades_.reserve(64);
    fills_.reserve(64);
}

BarExchange::Result BarExchange::addLimit(OrderId id, Side side, Price price, Quantity qty) {
    if (price < minPrice_ || price > maxPrice_) return OutOfBand;
    if (ourRemaining_.count(id)) return DuplicateId;
    if (!pending_.empty()) return MixedTypes;
    if (side == Side::Buy) {
        auto ask = book_.bestAsk();
        if (ask && price >= *ask) return SelfCross;
    } else {
        auto bid = book_.bestBid();
        if (bid && price <= *bid) return SelfCross;
    }
    book_.addOrder(id, side, OrderType::Limit, price, qty, trades_);  // just rests
    trades_.clear();
    ourRemaining_[id] = qty;
    return Ok;
}

BarExchange::Result BarExchange::addMarket(OrderId id, Side side, Quantity qty, int povBps) {
    if (!ourRemaining_.empty()) return MixedTypes;
    pending_.push_back({id, side, qty, povBps});
    return Ok;
}

bool BarExchange::cancel(OrderId id) {
    auto it = ourRemaining_.find(id);
    if (it != ourRemaining_.end()) {
        book_.cancelOrder(id);
        ourRemaining_.erase(it);
        return true;
    }
    auto p = std::find_if(pending_.begin(), pending_.end(),
                          [id](const Pending& x) { return x.id == id; });
    if (p == pending_.end()) return false;
    pending_.erase(p);
    return true;
}

void BarExchange::record(const Trade& t, Side ourSide) {
    OrderId ours = ourSide == Side::Buy ? t.buyId : t.sellId;
    fills_.push_back({ours, t.quantity, t.price});
    auto it = ourRemaining_.find(ours);
    if (it != ourRemaining_.end()) {
        it->second -= t.quantity;
        if (it->second == 0) ourRemaining_.erase(it);
    }
}

// Synthetic IOC against our resting orders. Returns shares filled.
int64_t BarExchange::sweep(Side synthSide, Price price, int64_t qty) {
    if (qty <= 0) return 0;
    trades_.clear();
    book_.addOrder(nextSynth_++, synthSide, OrderType::IOC, clamp(price),
                   static_cast<Quantity>(qty), trades_);
    int64_t done = 0;
    Side ourSide = synthSide == Side::Sell ? Side::Buy : Side::Sell;
    for (const Trade& t : trades_) {
        record(t, ourSide);
        done += t.quantity;
    }
    return done;
}

// Asks at typical + halfSpread + k (or bids at typical - halfSpread - k),
// levelSize shares each, `total` shares in all.
void BarExchange::postLadder(Side side, Price typical, int64_t total, int64_t levelSize) {
    int64_t posted = 0;
    for (int64_t k = 0; posted < total; ++k) {
        int64_t q = std::min(levelSize, total - posted);
        Price px = side == Side::Sell ? typical + halfSpread_ + k : typical - halfSpread_ - k;
        OrderId id = nextSynth_++;
        trades_.clear();
        book_.addOrder(id, side, OrderType::Limit, px, static_cast<Quantity>(q), trades_);
        ladder_.push_back(id);
        posted += q;
    }
}

const std::vector<FillRec>& BarExchange::processBar(Price high, Price low, Price typical,
                                                    int64_t volume) {
    fills_.clear();
    const int64_t cap = volume * capBps_ / 10'000;
    int64_t used[2] = {0, 0};  // [buy, sell]

    if (!ourRemaining_.empty()) {
        used[0] = sweep(Side::Sell, low + 1, cap);   // hits our bids above the low
        used[1] = sweep(Side::Buy, high - 1, cap);   // lifts our asks below the high
    }

    if (!pending_.empty()) {
        const int64_t levelSize = std::max<int64_t>(1, volume * depthBps_ / 10'000);
        bool posted[2] = {false, false};
        int64_t walked[2] = {0, 0};
        for (const Pending& p : pending_) {
            int s = p.side == Side::Buy ? 0 : 1;
            int64_t want = p.qty;
            if (p.povBps > 0) want = std::min<int64_t>(want, volume * p.povBps / 10'000);
            want = std::min(want, cap - used[s] - walked[s]);
            if (want <= 0) continue;
            if (!posted[s]) {
                // the ladder sits on the other side of the book from our order
                postLadder(p.side == Side::Buy ? Side::Sell : Side::Buy, typical,
                           cap - used[s], levelSize);
                posted[s] = true;
            }
            trades_.clear();
            book_.addOrder(p.id, p.side, OrderType::Market, 0,
                           static_cast<Quantity>(want), trades_);
            for (const Trade& t : trades_) {
                record(t, p.side);
                walked[s] += t.quantity;
            }
        }
        pending_.clear();
        for (OrderId id : ladder_) book_.cancelOrder(id);
        ladder_.clear();
    }
    return fills_;
}
