#pragma once

#include <cstdint>
#include <unordered_map>
#include <vector>

#include "FastOrderBook.h"

// The backtester's fill model on top of the real order book (FastOrderBook).
// Same rules as SimExchange in algo/core/exchange.py, read that docstring first.
//
// Per bar:
//  1. a synthetic seller sends an IOC sell at low+1 for `cap` shares. It
//     matches our resting bids that the bar traded through, best price first,
//     then FIFO, because that's just how the book matches.
//  2. same on the other side: synthetic IOC buy at high-1 against our asks
//  3. if we have market orders, post a synthetic ladder of asks (and/or bids)
//     around the typical price, send our market orders into the book so they
//     walk it, then cancel what's left of the ladder
//
// Our order ids come from Python and are small. Synthetic ids start at 2^40.
struct FillRec {
    OrderId  orderId;
    Quantity qty;
    Price    price;
};

class BarExchange {
public:
    BarExchange(Price minPrice, Price maxPrice, int capBps, int halfSpreadTicks,
                int depthBps);

    // Result codes for addLimit / addMarket. The checks live here rather than
    // in Python so the Python side doesn't need its own copy of the book.
    enum Result : int { Ok = 0, OutOfBand = 1, DuplicateId = 2, SelfCross = 3, MixedTypes = 4 };

    // A limit order rests until the next bar. Between bars the only orders in
    // the book are ours, so best bid/ask are our own quotes: a new order that
    // would cross them is refused (SelfCross).
    Result addLimit(OrderId id, Side side, Price price, Quantity qty);
    // Market orders wait for the next bar. Refused while we have limit orders resting.
    Result addMarket(OrderId id, Side side, Quantity qty, int povBps);
    bool cancel(OrderId id);

    const std::vector<FillRec>& processBar(Price high, Price low, Price typical,
                                           int64_t volume);

    size_t restingCount() const { return ourRemaining_.size(); }
    Price  minPrice() const { return minPrice_; }
    Price  maxPrice() const { return maxPrice_; }

private:
    struct Pending {
        OrderId  id;
        Side     side;
        Quantity qty;
        int      povBps;
    };

    Price clamp(Price p) const { return p < minPrice_ ? minPrice_ : (p > maxPrice_ ? maxPrice_ : p); }
    int64_t sweep(Side synthSide, Price price, int64_t qty);
    void postLadder(Side side, Price typical, int64_t total, int64_t levelSize);
    void record(const Trade& t, Side ourSide);

    FastOrderBook book_;
    Price minPrice_, maxPrice_;
    int capBps_, halfSpread_, depthBps_;

    std::unordered_map<OrderId, Quantity> ourRemaining_;  // our resting limit orders
    std::vector<Pending> pending_;                          // market orders for next bar
    std::vector<OrderId> ladder_;                           // synthetic ids to cancel
    std::vector<Trade> trades_;
    std::vector<FillRec> fills_;
    OrderId nextSynth_ = OrderId{1} << 40;
};
