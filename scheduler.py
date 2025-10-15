# scheduler.py - جدولة المهام التلقائية
from apscheduler.schedulers.background import BackgroundScheduler
import time
from datetime import datetime
import requests
from database import cleanup_expired, get_active_users
from config import CHECK_EXPIRE_HOURS, API_URL
# send_signal_to_user will be imported dynamically in runtime to avoid circular import

scheduler = BackgroundScheduler()

def check_expired_job():
    try:
        cleanup_expired()
        print("[Scheduler] cleanup_expired ran", datetime.utcnow())
    except Exception as e:
        print("[Scheduler] cleanup error:", e)

def fetch_xau_price():
    try:
        resp = requests.get(API_URL, timeout=10)
        data = resp.json()
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        return float(price)
    except Exception as e:
        print("[Scheduler] price fetch error:", e)
        return None

def generate_signals_job(send_signal):
    price = fetch_xau_price()
    if price is None:
        print("[Scheduler] no price, skipping signals")
        return
    import random
    signals = []
    count = random.randint(0,2)
    for i in range(count):
        typ = random.choice(["BUY","SELL"]) if random.random()>0.2 else "BUY"
        mode = "SCALP" if random.random()<0.75 else "LONG"
        sl = round(price - (0.5 if mode=="SCALP" else 5.0) * (1 if typ=="SELL" else -1), 2)
        tp = round(price + (1.0 if mode=="SCALP" else 15.0) * (1 if typ=="BUY" else -1), 2)
        success_rate = random.randint(70,90) if mode=="SCALP" else random.randint(80,95)
        signals.append({
            "type": typ,
            "mode": mode,
            "entry": round(price,2),
            "sl": sl,
            "tp": tp,
            "rate": success_rate,
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        })
    users = get_active_users()
    for user in users:
        for s in signals:
            try:
                send_signal(user['telegram_id'], s)
            except Exception as e:
                print("[Scheduler] send signal error", e)

def start_scheduler(send_signal):
    scheduler.add_job(check_expired_job, 'interval', hours=CHECK_EXPIRE_HOURS, id='cleanup_job', replace_existing=True)
    scheduler.add_job(lambda: generate_signals_job(send_signal), 'cron', minute=5, id='signals_job', replace_existing=True)
    scheduler.start()
    print("[Scheduler] started")
