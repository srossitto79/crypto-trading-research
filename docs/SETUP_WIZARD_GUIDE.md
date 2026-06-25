# Setup Wizard Guide - Exchange Configuration

## Overview

The setup wizard now supports **5 major exchanges** plus a generic CCXT option for 90+ others:

1. **Hyperliquid** (default)
2. **Binance** (most popular)
3. **Kraken** (US-friendly)
4. **OKX** (advanced features)
5. **Coinbase** (simplest)
6. **Generic CCXT** (any of 100+ exchanges)

---

## Step 1: Choose Your Exchange

In the Settings UI, go to **Exchange** section:

### Option A: Keep Hyperliquid (Default)
```
Exchange: hyperliquid
Wallet Address: [your ETH wallet]
API Address: [auto-derived or manual]
Private Key: [your Hyperliquid private key]
Use Testnet: true/false
```

### Option B: Switch to Binance
```
Exchange: binance
API Key: [your Binance API key]
API Secret: [your Binance API secret]
Use Testnet: true  # Start with testnet!
```

### Option C: Switch to Kraken
```
Exchange: kraken
API Key: [your Kraken API key]
API Secret: [your Kraken API secret]
Use Testnet: true  # Kraken supports testnet
```

### Option D: Switch to OKX
```
Exchange: okx
API Key: [your OKX API key]
API Secret: [your OKX API secret]
Passphrase: [your OKX passphrase]  # OKX requires this
Use Testnet: true
```

### Option E: Switch to Coinbase
```
Exchange: coinbase
API Key: [your Coinbase API key]
API Secret: [your Coinbase API secret]
```

### Option F: Use Any CCXT Exchange
```
Exchange: generic_ccxt
Exchange ID: [kraken, huobi, gateio, mexc, etc]
API Key: [your API key]
API Secret: [your API secret]
Use Testnet: true (if available)
```

---

## Step 2: Get API Keys for Your Exchange

### For Binance:
1. Go to https://www.binance.com/en/user/settings/api-management
2. Create new API key
3. Enable "Spot Trading" permissions
4. Copy API Key and Secret
5. For testnet: https://testnet.binance.vision

### For Kraken:
1. Go to Settings → API
2. Create new key
3. Select "Query Funds", "Query Orders", "Query Trades", "Create & Modify Orders", "Cancel Orders"
4. Copy API Key and Private Key
5. Testnet: https://demo-futures.kraken.com

### For OKX:
1. Go to Account → API
2. Create new API key
3. Select appropriate permissions
4. Copy API Key, Secret Key, and Passphrase
5. Testnet: https://www.okx.com (select testnet in settings)

### For Coinbase:
1. Go to Settings → API
2. Create new key with appropriate scopes
3. Copy API Key and API Secret
4. Note: Coinbase doesn't have a formal testnet

### For Generic CCXT:
1. Follow the exchange's API documentation
2. Get API Key and Secret
3. Some exchanges require additional credentials (passphrase, etc.)

---

## Step 3: Configure in Axiom

### Via UI (Settings):

1. Go to **Settings** → **Exchange**
2. Select your exchange from dropdown
3. Enter API credentials
4. Toggle **Use Testnet** to `true`
5. Click **Save**

### Via Environment Variables:

```bash
# .env file

# For Binance
EXCHANGE=binance
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
BINANCE_TESTNET=true

# For Kraken
EXCHANGE=kraken
KRAKEN_API_KEY=your_key_here
KRAKEN_API_SECRET=your_secret_here
KRAKEN_TESTNET=true

# For OKX
EXCHANGE=okx
OKX_API_KEY=your_key_here
OKX_API_SECRET=your_secret_here
OKX_API_PASSPHRASE=your_passphrase_here
OKX_TESTNET=true

# For generic CCXT
EXCHANGE=generic_ccxt
GENERIC_CCXT_EXCHANGE=huobi  # or any other exchange ID
GENERIC_CCXT_API_KEY=your_key_here
GENERIC_CCXT_API_SECRET=your_secret_here
GENERIC_CCXT_TESTNET=true
```

Then restart the backend:

```bash
python -m axiom api
```

---

## Step 4: Verify Configuration

### Check API Key Status

In Settings UI, look for a checkmark or "Connected" badge next to each exchange:

- ✅ **Hyperliquid**: Shows "has_key" when private key is configured
- ✅ **Binance**: Shows "has_key" when API key + secret are configured
- ✅ **Kraken**: Shows "has_key" when API key + secret are configured
- ✅ **OKX**: Shows "has_key" when API key + secret + passphrase are configured
- ✅ **Coinbase**: Shows "has_key" when API key + secret are configured
- ✅ **Generic CCXT**: Shows "has_key" when exchange ID + API credentials are configured

### Test Connection

```bash
python -m axiom soak
```

Should show something like:
```
[OK] Exchange connection verified: binance
[OK] Account value: $1,234.56
[OK] Positions: 0 open
```

---

## Recommended Setup Flow

### For Complete Beginners:

1. **Start with MockExchange** (no setup needed):
   - In Settings, set Exchange to "mock"
   - Paper trade safely with zero risk
   - This tests your strategy logic

2. **Test on Testnet**:
   - Choose Binance or Kraken (both have good testnet)
   - Get testnet API keys
   - Configure in Axiom
   - Set `Use Testnet: true`
   - Run small test trades

3. **Go Live**:
   - Start with real capital on spot trading only
   - Use small position sizes
   - Monitor logs carefully

### For Experienced Traders:

1. **Start on Preferred Exchange**:
   - Binance for maximum liquidity
   - Kraken for US compliance
   - OKX for advanced features

2. **Use Production from Day One** (if confident):
   - Skip testnet if you understand the risks
   - Start with small position sizes
   - Scale up over time

---

## Troubleshooting

### "Invalid API Key" Error

**Causes**:
- Key/secret copied incorrectly (extra spaces?)
- Key has been revoked on exchange
- IP whitelist doesn't include your server
- Key doesn't have required permissions

**Solution**:
1. Generate new API key on exchange
2. Carefully copy without extra spaces
3. Ensure "API trading" or "Spot trading" is enabled
4. Add your IP to whitelist (if required)
5. Wait 1-2 minutes for key to activate
6. Test again

### "Exchange Not Supported"

**Cause**: Invalid exchange ID or typo

**Solution**:
For generic CCXT, use exact ID from CCXT:
- `binance` not `Binance`
- `kraken` not `Kraken`
- `gateio` not `Gate.io`

See [CCXT Supported Exchanges](https://docs.ccxt.com/manual/docs/exchange-markets) for complete list.

### Settings Don't Persist

**Cause**: Backend not restarted after environment variable change

**Solution**:
```bash
# Kill the backend process
Ctrl+C

# Restart
python -m axiom api
```

### "Feature Not Supported"

**Cause**: Exchange doesn't support a feature (e.g., leverage on spot exchanges)

**Solution**:
- Some exchanges only support spot trading (no leverage)
- Others support margin/futures
- Check exchange capabilities in logs
- See [Exchange Comparison](EXCHANGE_CHOICE_GUIDE.md) table

---

## Security Best Practices

### Do's:
✅ Use **API keys** (not account passwords)  
✅ Enable **IP whitelisting** where possible  
✅ Restrict permissions to **"Spot Trading"** only  
✅ Keep API secrets in `.env` (not in code)  
✅ Rotate keys periodically  
✅ Use **testnet first** before real capital  
✅ Start with **small position sizes**  

### Don'ts:
❌ Don't use account passwords as API keys  
❌ Don't commit API keys to Git  
❌ Don't give "withdraw" permission to trading bot  
❌ Don't enable full account access  
❌ Don't skip IP whitelisting  
❌ Don't test on mainnet with real capital  

---

## Exchange Capabilities Comparison

| Feature | Hyperliquid | Binance | Kraken | OKX | Coinbase |
|---------|---|---|---|---|---|
| **Spot Trading** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Margin Trading** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Perpetuals** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Leverage** | 1-50x | 1-125x | 1-50x | 1-125x | ❌ |
| **Testnet** | ❌ | ✅ | ✅ | ✅ | ❌ |
| **API Rate Limit** | Variable | 1200/min | 15/sec | 40/sec | Variable |
| **Fees** | Variable | 0.1% | 0.26% | 0.08% | 0.5%+ |
| **US Available** | ❌ | ✅ | ✅ | ❌ | ✅ |

---

## Common Configurations

### Configuration 1: Safe Testing (Recommended)
```
Exchange: binance
Use Testnet: true
Initial Capital: $1,000
Trading Mode: paper
```
**Why**: Testnet is free, real prices, zero risk

### Configuration 2: Paper Trading with Real Prices
```
Exchange: mock (MockExchange)
Initial Capital: $10,000
Trading Mode: paper
```
**Why**: Fastest, zero setup, realistic execution

### Configuration 3: Live Spot Trading
```
Exchange: binance
Use Testnet: false
Initial Capital: $5,000  (start small!)
Trading Mode: live
Max Position Size: 5%
```
**Why**: Real capital, real prices, manageable risk

### Configuration 4: Advanced Futures Trading
```
Exchange: okx
Use Testnet: true
Initial Capital: $1,000
Leverage: 2x-5x  (start conservative!)
```
**Why**: OKX has excellent futures features

---

## Next Steps

1. **Choose your exchange** from the list above
2. **Get API keys** using the instructions in "Step 2"
3. **Configure in Axiom** via Settings or .env
4. **Verify connection** with `python -m axiom soak`
5. **Test with small capital** before scaling up
6. **Monitor logs** for any issues

---

## Documentation Links

- [Exchange Choice Guide](EXCHANGE_CHOICE_GUIDE.md) - Which exchange to use
- [CCXT Integration Guide](CCXT_INTEGRATION.md) - Full technical details
- [CCXT Supported Exchanges](https://docs.ccxt.com/manual/docs/exchange-markets) - All 100+ exchanges
- [Binance API Docs](https://binance-docs.github.io/apidocs/)
- [Kraken API Docs](https://docs.kraken.com/rest/)
- [OKX API Docs](https://www.okx.com/docs/en/)
- [Coinbase API Docs](https://docs.cloud.coinbase.com/)

---

## Support

If you encounter issues:

1. Check the **Troubleshooting** section above
2. Verify API key permissions on the exchange
3. Check backend logs: `python -m axiom api` (verbose output)
4. Check frontend console (F12 → Console tab)
5. Try with MockExchange first to isolate the issue

---

**Status**: Setup wizard fully supports 5 major exchanges + 90+ via generic CCXT  
**Testnet Available**: Binance, Kraken, OKX  
**Capital Required**: Zero (use MockExchange or testnet)  
**Setup Time**: 10 minutes
