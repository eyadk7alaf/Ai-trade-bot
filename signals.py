import random, datetime
from market import get_price
def generate_signal():
    symbols = ['XAUUSD','EURUSD','GBPUSD']
    signal = {}
    signal['symbol'] = random.choice(symbols)
    signal['type'] = random.choice(['Buy','Sell'])
    signal['mode'] = 'Auto'
    price = get_price(signal['symbol'])
    signal['entry'] = round(price,2)
    signal['sl'] = round(price*0.995,2)
    signal['tp'] = round(price*1.005,2)
    signal['rate'] = random.randint(70,90)
    signal['time'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return signal