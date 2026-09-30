// pybind11 bindings: the order book itself, plus BarExchange (the fill model
// in C++). Python sees a module called algo_cpp.
//
// Crossing the Python/C++ boundary costs something every call (argument
// conversion, building the return list), so BarExchange does a whole bar per
// call instead of exposing one call per match.

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "BarExchange.h"
#include "FastOrderBook.h"

namespace py = pybind11;

namespace {

py::list tradesToList(const std::vector<Trade>& trades) {
    py::list out(trades.size());
    for (size_t i = 0; i < trades.size(); ++i) {
        const Trade& t = trades[i];
        out[i] = py::make_tuple(t.buyId, t.sellId, t.price, t.quantity);
    }
    return out;
}

}  // namespace

PYBIND11_MODULE(algo_cpp, m) {
    m.doc() = "C++20 limit order book and bar-level exchange";

    py::enum_<Side>(m, "Side")
        .value("Buy", Side::Buy)
        .value("Sell", Side::Sell);

    py::enum_<OrderType>(m, "OrderType")
        .value("Limit", OrderType::Limit)
        .value("Market", OrderType::Market)
        .value("IOC", OrderType::IOC)
        .value("FOK", OrderType::FOK);

    // The order book from the limit-order-book project, as is.
    py::class_<FastOrderBook>(m, "OrderBook")
        .def(py::init<Price, Price, size_t>(), py::arg("min_price") = 0,
             py::arg("max_price") = 100'000, py::arg("expected_orders") = 1 << 16)
        .def(
            "add_order",
            [](FastOrderBook& b, OrderId id, Side side, OrderType type, Price price,
               Quantity qty) { return tradesToList(b.addOrder(id, side, type, price, qty)); },
            py::arg("id"), py::arg("side"), py::arg("type"), py::arg("price"), py::arg("qty"),
            "Add an order and match it. Returns [(buy_id, sell_id, price, qty), ...].")
        .def("cancel_order", &FastOrderBook::cancelOrder)
        .def("best_bid", &FastOrderBook::bestBid)
        .def("best_ask", &FastOrderBook::bestAsk)
        .def("volume_at", &FastOrderBook::volumeAt)
        .def("order_count", &FastOrderBook::orderCount);

    py::class_<BarExchange>(m, "BarExchange")
        .def(py::init<Price, Price, int, int, int>(), py::arg("min_price"),
             py::arg("max_price"), py::arg("cap_bps"), py::arg("half_spread_ticks"),
             py::arg("depth_bps"))
        .def("add_limit",
             [](BarExchange& ex, OrderId id, Side side, Price px, Quantity qty) {
                 return static_cast<int>(ex.addLimit(id, side, px, qty));
             })
        .def("add_market",
             [](BarExchange& ex, OrderId id, Side side, Quantity qty, int povBps) {
                 return static_cast<int>(ex.addMarket(id, side, qty, povBps));
             })
        .def("cancel", &BarExchange::cancel)
        .def(
            "process_bar",
            [](BarExchange& ex, Price high, Price low, Price typical, int64_t volume) {
                const auto& fills = ex.processBar(high, low, typical, volume);
                py::list out(fills.size());
                for (size_t i = 0; i < fills.size(); ++i)
                    out[i] = py::make_tuple(fills[i].orderId, fills[i].qty, fills[i].price);
                return out;
            },
            "Match one bar. Returns [(order_id, qty, price), ...] for our orders.")
        .def("resting_count", &BarExchange::restingCount)
        .def_property_readonly("min_price", &BarExchange::minPrice)
        .def_property_readonly("max_price", &BarExchange::maxPrice);
}
