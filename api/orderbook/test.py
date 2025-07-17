# Test script
import orderbook_cpp as ob

book = ob.Orderbook()

# Test new token-aware method
trades = book.add_order(
    ob.OrderType.GoodTillCancel,
    ob.Side.Buy,
    6500,  # $65.00
    100,   # quantity
    "alice",
    ob.Token.YES  # NEW: specify token
)

print(f"Trades: {len(trades)}")
print(f"Orders: {book.size()}")