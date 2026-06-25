
import ccxt
from axiom.data import get_exchange

def check_options():
    binance = get_exchange("binance")
    print(f"Binance options: {binance.options}")
    
    # Let's see if we can find any AUD references
    for key, val in binance.options.items():
        if "AUD" in str(val):
            print(f"Found AUD in option {key}: {val}")

if __name__ == "__main__":
    check_options()
