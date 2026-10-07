"""Close the paper-trading MU position with a market SELL via TWS (port 7497)."""
import sys
import time

from ib_async import IB, MarketOrder

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 11


def main() -> int:
    ib = IB()
    try:
        errors = []
        ib.errorEvent += lambda reqId, code, msg, contract: errors.append((reqId, code, msg))
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=10)

        accounts = ib.managedAccounts()
        if not accounts or not all(a.startswith("DU") for a in accounts):
            print(f"Refusing to trade: not a paper account {accounts}")
            return 2

        pos = next((p for p in ib.positions() if p.contract.symbol == "MU"), None)
        if pos is None or pos.position == 0:
            print("NO POSITION TO CLOSE")
            return 0

        contract = pos.contract
        contract.exchange = "SMART"
        order = MarketOrder("SELL", abs(pos.position))
        order.outsideRth = True
        trade = ib.placeOrder(contract, order)

        deadline = time.time() + 10
        while time.time() < deadline and not trade.isDone():
            ib.sleep(0.5)

        status = trade.orderStatus.status
        fill = trade.orderStatus.avgFillPrice if trade.orderStatus.filled else "pending"
        print(f"Sold quantity: {trade.orderStatus.filled} of {abs(pos.position)}")
        print(f"Fill price: {fill}")
        print(f"Status: {status}")

        if status in {"Cancelled", "ApiCancelled", "Inactive", "PendingSubmit"}:
            print("Order not accepted. trade.log:")
            for entry in trade.log:
                print(f"  {entry.time} {entry.status} {entry.message} {entry.errorCode or ''}")
            for req_id, code, msg in errors:
                print(f"  TWS error {code} (req {req_id}): {msg}")
            return 1
        return 0
    except Exception as exc:
        print(f"Error: {exc!r}")
        return 1
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
