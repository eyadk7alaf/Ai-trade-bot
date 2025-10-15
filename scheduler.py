import schedule, time
from signals import generate_signal
from database import get_active_users
def job(send_signal_func):
    users = get_active_users()
    signal = generate_signal()
    for u in users:
        send_signal_func(u['telegram_id'], signal)
def start_scheduler(send_signal_func):
    schedule.every().day.at("09:00").do(job, send_signal_func)
    while True:
        schedule.run_pending()
        time.sleep(10)