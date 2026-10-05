"""Read-only check against TWS: connected account, account summary, open positions.

Places no orders. Connects with readonly=True so the API session cannot submit orders.
Usage:  python ib_account_check.py [--host 127.0.0.1] [--port 7497] [--client-id 17]
"""
import argparse

from ib_async import IB

SUMMARY_TAGS = [
    "NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds",
    "ExcessLiquidity", "GrossPositionValue", "UnrealizedPnL", "RealizedPnL",
    "InitMarginReq", "MaintMarginReq",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497)
    p.add_argument("--client-id", type=int, default=17)
    args = p.parse_args()

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=10)
    try:
        accounts = ib.managedAccounts()
        print(f"Connected. Managed account(s): {', '.join(accounts)}")
        for acct in accounts:
            kind = "PAPER" if acct.startswith("D") else "LIVE?"
            print(f"  {acct}  ({kind} - paper accounts start with 'D')")

        print("\n=== Account summary ===")
        for v in ib.accountSummary():
            if v.tag in SUMMARY_TAGS:
                print(f"{v.account:<12} {v.tag:<20} {v.value:>18} {v.currency}")

        print("\n=== Open positions ===")
        positions = ib.positions()
        if not positions:
            print("(none)")
        for pos in positions:
            c = pos.contract
            print(f"{pos.account:<12} {c.secType:<5} {c.localSymbol or c.symbol:<20} "
                  f"qty={pos.position:>10g}  avgCost={pos.avgCost:,.4f}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
