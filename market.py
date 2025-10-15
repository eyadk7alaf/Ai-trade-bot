import yfinance as yf
def get_price(symbol):
    data = yf.download(symbol, period='1d', interval='1m')
    return float(data['Close'][-1])