"""FixExchange: the same Exchange interface, but orders go over FIX 4.4 to the
C++ gateway (github.com/abhiroopyalavarthi/fix-gateway) and match in its order book.

Two FIX sessions:
  ALGO - the strategy's own orders
  LP   - a liquidity provider that replays each bar into the gateway's book,
         following the same fill model as SimExchange (see exchange.py):
         a trade-through sweep of our resting limits, then a ladder of resting
         orders around the typical price for our market orders to walk, then
         cancels whatever is left of the ladder.

So a backtest over FIX should give the same fills as SimExchange, and
tests/test_fix_exchange.py checks exactly that. Every step waits for its
execution reports before the next one, because the two sessions are separate
TCP connections and the gateway could otherwise see them out of order.

Needs the `quickfix` package (uv sync --extra fix) and a running gateway.
"""

from __future__ import annotations

import itertools
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

from algo.core.exchange import Exchange, FillModel, OwnLimits, pov_bps
from algo.core.orders import Bar, Fill, Order, OrderStatus, OrderType, Side, to_ticks

try:
    import quickfix as fix
    import quickfix44 as fix44
except ImportError as e:  # pragma: no cover
    raise ImportError("FixExchange needs the quickfix package: uv sync --extra fix") from e

SPEC = Path(__file__).resolve().parent / "FIX44.xml"
TERMINAL = {"2", "4", "8"}  # Filled, Canceled, Rejected


class FixError(RuntimeError):
    pass


class _Reports:
    """Execution reports and cancel rejects per ClOrdID, filled in by the QuickFIX thread."""

    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.by_id: dict[str, list[dict]] = defaultdict(list)
        self.algo_log: list[tuple[str, dict]] = []  # ALGO's reports in the order they arrived
        self.logged_on: set[str] = set()

    def add(self, session: str, clordid: str, rep: dict) -> None:
        with self.cv:
            self.by_id[clordid].append(rep)
            if session == "ALGO":
                self.algo_log.append((clordid, rep))
            self.cv.notify_all()

    def wait(self, pred, timeout: float, what: str) -> None:
        with self.cv:
            if not self.cv.wait_for(pred, timeout):
                raise FixError(f"timed out after {timeout}s waiting for {what}")


class _App(fix.Application):
    def __init__(self, reports: _Reports) -> None:
        super().__init__()
        self.r = reports

    def onCreate(self, sid):  # noqa: N802 (QuickFIX's names)
        pass

    def onLogon(self, sid):  # noqa: N802
        with self.r.cv:
            self.r.logged_on.add(sid.getSenderCompID().getValue())
            self.r.cv.notify_all()

    def onLogout(self, sid):  # noqa: N802
        with self.r.cv:
            self.r.logged_on.discard(sid.getSenderCompID().getValue())
            self.r.cv.notify_all()

    def toAdmin(self, msg, sid):  # noqa: N802
        pass

    def fromAdmin(self, msg, sid):  # noqa: N802
        pass

    def toApp(self, msg, sid):  # noqa: N802
        pass

    def fromApp(self, msg, sid):  # noqa: N802
        get = lambda tag: msg.getField(tag) if msg.isSetField(tag) else ""  # noqa: E731
        mtype = msg.getHeader().getField(35)
        if mtype == "8":
            rep = {"type": "8", "exec": get(150), "status": get(39), "cum": int(float(get(14) or 0)),
                   "leaves": int(float(get(151) or 0)), "last_qty": int(float(get(32) or 0)),
                   "last_px": get(31), "orig": get(41), "text": get(58)}
        elif mtype == "9":
            rep = {"type": "9", "status": get(39), "orig": get(41), "text": get(58)}
        else:
            return
        self.r.add(sid.getSenderCompID().getValue(), get(11), rep)


class FixExchange(Exchange):
    def __init__(self, model: FillModel | None = None, host: str = "127.0.0.1", port: int = 9878,
                 timeout: float = 10.0) -> None:
        super().__init__(model)
        self.timeout = timeout
        self._tag = f"{int(time.time() * 1000) % 10**9:09d}"  # ClOrdIDs unique per run
        self._seq = itertools.count(1)
        self._reports = _Reports()
        self._app = _App(self._reports)

        self._orders: dict[int, Order] = {}
        self._own = OwnLimits()
        self._pending_mkt: dict[str, list[Order]] = defaultdict(list)
        self._clordid: dict[int, str] = {}      # our order id -> ALGO ClOrdID
        self._by_clordid: dict[str, Order] = {}
        self._applied = 0  # how far into the ALGO report log we've turned reports into fills
        self._awaiting: list[tuple[str, str]] = []  # (clordid, what) to settle before a bar

        self._dir = tempfile.TemporaryDirectory(prefix="fixexchange-")
        settings = fix.SessionSettings()
        d = fix.Dictionary()
        for k, v in {"ConnectionType": "initiator", "BeginString": "FIX.4.4", "TargetCompID": "GATEWAY",
                     "SocketConnectHost": host, "SocketConnectPort": str(port), "StartTime": "00:00:00",
                     "EndTime": "00:00:00", "HeartBtInt": "30", "ReconnectInterval": "1",
                     "ResetOnLogon": "Y", "SocketNodelay": "Y", "UseDataDictionary": "Y",
                     "DataDictionary": str(SPEC), "FileLogPath": f"{self._dir.name}/log"}.items():
            d.setString(k, v)
        settings.set(d)
        for sender in ("ALGO", "LP"):
            s = fix.Dictionary()
            s.setString("SenderCompID", sender)
            settings.set(fix.SessionID("FIX.4.4", sender, "GATEWAY"), s)
        self._sid = {s: fix.SessionID("FIX.4.4", s, "GATEWAY") for s in ("ALGO", "LP")}
        self._init = fix.SocketInitiator(self._app, fix.MemoryStoreFactory(), settings,
                                         fix.FileLogFactory(settings))
        self._init.start()
        self._reports.wait(lambda: {"ALGO", "LP"} <= self._reports.logged_on, timeout,
                           f"logon to the gateway at {host}:{port}")

    def close(self) -> None:
        if self._init is None:
            return
        self._init.stop()
        # QuickFIX keeps a global registry of sessions until the initiator is
        # destroyed; drop it now so the next FixExchange can use the same session ids
        self._init = None
        self._dir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- sending -----------------------------------------------------------
    def _send_new(self, session: str, clordid: str, symbol: str, side: Side, qty: int,
                  price: int | None, tif: str = "0") -> None:
        m = fix44.NewOrderSingle()
        m.setField(fix.ClOrdID(clordid))
        m.setField(fix.Symbol(symbol))
        m.setField(fix.Side("1" if side is Side.BUY else "2"))
        m.setField(fix.TransactTime())
        m.setField(fix.OrderQty(qty))
        if price is None:
            m.setField(fix.OrdType("1"))
        else:
            m.setField(fix.OrdType("2"))
            m.setField(44, f"{price // 100}.{price % 100:02d}")  # exact decimal text
        m.setField(fix.TimeInForce(tif))
        fix.Session.sendToTarget(m, self._sid[session])

    def _send_cancel(self, session: str, clordid: str, orig: str, symbol: str, side: Side) -> None:
        m = fix44.OrderCancelRequest()
        m.setField(fix.OrigClOrdID(orig))
        m.setField(fix.ClOrdID(clordid))
        m.setField(fix.Symbol(symbol))
        m.setField(fix.Side("1" if side is Side.BUY else "2"))
        m.setField(fix.TransactTime())
        fix.Session.sendToTarget(m, self._sid[session])

    def _new_id(self, kind: str) -> str:
        return f"{self._tag}-{kind}{next(self._seq)}"

    # --- waiting -------------------------------------------------------------
    def _reps(self, clordid: str) -> list[dict]:
        with self._reports.cv:
            return list(self._reports.by_id.get(clordid, []))

    def _wait_ack(self, clordid: str) -> dict:
        self._reports.wait(lambda: bool(self._reports.by_id.get(clordid)), self.timeout, f"ack of {clordid}")
        return self._reps(clordid)[0]

    def _wait_terminal(self, clordid: str) -> list[dict]:
        def done():
            reps = self._reports.by_id.get(clordid, [])
            return any(r["type"] == "9" or r["status"] in TERMINAL for r in reps)
        self._reports.wait(done, self.timeout, f"final report for {clordid}")
        return self._reps(clordid)

    def _apply_algo_reports(self, bar: Bar) -> None:
        """Turn new ALGO trade reports into Fill callbacks, in the order the gateway sent them."""
        with self._reports.cv:
            new = self._reports.algo_log[self._applied:]
            self._applied = len(self._reports.algo_log)
        for cid, r in new:
            order = self._by_clordid.get(cid)
            if order is None or r["type"] != "8":
                continue
            if r["exec"] == "F":
                self._fill(order, r["last_qty"], to_ticks(float(r["last_px"])), bar)
            elif r["exec"] == "8":
                order.status = OrderStatus.CANCELLED
                self._forget(order)
                raise FixError(f"gateway rejected order {order.id}: {r['text']}")

    def _algo_filled(self, order: Order) -> int:
        cid = self._clordid[order.id]
        return max((r["cum"] for r in self._reps(cid) if r["type"] == "8"), default=0)

    # --- Exchange interface -----------------------------------------------------
    def send_order(self, order: Order) -> None:
        self._validate(order)
        if order.id in self._orders:
            raise ValueError(f"duplicate order id {order.id}")
        sym = order.symbol
        cid = self._new_id("A")
        if order.type is OrderType.LIMIT:
            if self._pending_mkt.get(sym):
                raise ValueError(f"{sym}: limit order while market orders are pending")
            self._own.check(order)
            self._own.add(order)
            self._send_new("ALGO", cid, sym, order.side, order.qty, order.limit_px)
            self._awaiting.append((cid, "ack"))
        else:
            if self._own.any(sym):
                raise ValueError(f"{sym}: market order while limit orders are resting")
            self._pending_mkt[sym].append(order)  # goes out when the next bar is replayed
        self._orders[order.id] = order
        self._clordid[order.id] = cid
        self._by_clordid[cid] = order

    def cancel(self, order_id: int) -> bool:
        order = self._orders.get(order_id)
        if order is None or not order.is_open:
            return False
        if order.type is OrderType.MARKET:
            self._pending_mkt[order.symbol].remove(order)
        else:
            cxl = self._new_id("C")
            self._send_cancel("ALGO", cxl, self._clordid[order.id], order.symbol, order.side)
            self._awaiting.append((cxl, "terminal"))
            self._own.remove(order)
        order.status = OrderStatus.CANCELLED
        return True

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        return [o for o in self._orders.values() if o.is_open and (symbol is None or o.symbol == symbol)]

    def _forget(self, order: Order) -> None:
        if order.type is OrderType.LIMIT:
            self._own.remove(order)

    def _fill(self, order: Order, qty: int, price: int, bar: Bar) -> None:
        order.filled += qty
        order.status = OrderStatus.FILLED if order.remaining == 0 else OrderStatus.PARTIAL
        if order.status is OrderStatus.FILLED:
            self._forget(order)
        self._on_fill(Fill(order.id, order.symbol, order.side, qty, price, bar.ts))

    # --- one bar -----------------------------------------------------------------
    def process_bar(self, bar: Bar) -> None:
        # everything the strategy sent since the last bar has to be in the book first
        for cid, what in self._awaiting:
            self._wait_ack(cid) if what == "ack" else self._wait_terminal(cid)
        self._awaiting.clear()

        m = self.model
        sym = bar.symbol
        cap = m.cap(bar.volume)
        used = {Side.BUY: 0, Side.SELL: 0}

        if self._own.any(sym):
            resting = [o for o in self._own.values(sym)]
            before = {o.id: o.filled for o in resting}
            # the bar's sellers sweep our bids above its low, its buyers our asks below its high
            for synth_side, px in ((Side.SELL, bar.low + 1), (Side.BUY, bar.high - 1)):
                if cap <= 0:
                    break
                cid = self._new_id("S")
                self._send_new("LP", cid, sym, synth_side, cap, px, tif="3")  # IOC
                lp = self._wait_terminal(cid)
                swept = max(r["cum"] for r in lp if r["type"] == "8")
                our_side = synth_side.opposite()
                used[our_side] = swept
                if swept:
                    # our side of those trades arrives on the ALGO session; wait for all of it
                    self._reports.wait(
                        lambda s=our_side, n=swept: sum(
                            self._algo_filled(o) - before[o.id] for o in resting if o.side is s) >= n,
                        self.timeout, "ALGO side of the sweep")
            self._apply_algo_reports(bar)

        pending = self._pending_mkt.get(sym)
        if pending:
            level = m.level_size(bar.volume)
            ladder: dict[Side, list[tuple[str, Side]]] = {}
            walked = {Side.BUY: 0, Side.SELL: 0}
            for order in pending:
                side = order.side
                want = order.remaining
                pb = pov_bps(order.max_pov)
                if pb:
                    want = min(want, bar.volume * pb // 10_000)
                want = min(want, cap - used[side] - walked[side])
                if want <= 0:
                    order.status = OrderStatus.CANCELLED
                    continue
                if side not in ladder:
                    ladder[side] = self._post_ladder(side, bar, cap - used[side], level)
                cid = self._clordid[order.id]
                self._send_new("ALGO", cid, sym, side, want, None)
                reps = self._wait_terminal(cid)
                walked[side] += max(r["cum"] for r in reps if r["type"] == "8")
                self._apply_algo_reports(bar)
                if order.is_open:  # IOC: what didn't fill is gone
                    order.status = OrderStatus.CANCELLED
            self._pending_mkt[sym] = []
            for posts in ladder.values():
                cxls = []
                for lid, lside in posts:
                    cxl = self._new_id("X")
                    self._send_cancel("LP", cxl, lid, sym, lside)
                    cxls.append(cxl)
                for cxl in cxls:
                    self._wait_terminal(cxl)

    def _post_ladder(self, our_side: Side, bar: Bar, total: int, level: int) -> list[tuple[str, Side]]:
        """Resting LP orders on the other side of the book, one tick apart."""
        lp_side = our_side.opposite()
        posts, posted, k = [], 0, 0
        while posted < total:
            q = min(level, total - posted)
            px = bar.typical + our_side.sign * (self.model.half_spread_ticks + k)
            cid = self._new_id("L")
            self._send_new("LP", cid, bar.symbol, lp_side, q, px)
            posts.append((cid, lp_side))
            posted += q
            k += 1
        for cid, _ in posts:
            ack = self._wait_ack(cid)
            if ack["type"] == "8" and ack["exec"] == "8":
                raise FixError(f"gateway rejected LP order: {ack['text']}")
        return posts
