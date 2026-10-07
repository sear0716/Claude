# Live check against a running TWS paper session. Run it directly:
#     python gap_bot/tests/test_connect.py
# Under pytest it is skipped, so the offline test suite never tries to reach TWS.
if __name__ != "__main__":
    import pytest
    pytest.skip("needs a running TWS; run directly: python gap_bot/tests/test_connect.py", allow_module_level=True)

from ib_async import IB

ib = IB()
ib.connect('127.0.0.1', 7497, clientId=99)
print('Connected:', ib.isConnected())
print('Accounts:', ib.managedAccounts())
ib.disconnect()
