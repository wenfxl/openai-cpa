import threading
import time
import re
from collections import OrderedDict
from utils import config as cfg


global_code_pool = {}
code_pool_lock = threading.Lock()


class BoundedSet:
    def __init__(self, max_size=10000):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = threading.Lock()

    def add(self, key):
        with self.lock:
            self.cache[key] = True
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def __contains__(self, key):
        with self.lock:
            return key in self.cache


processed_msg_ids = BoundedSet(max_size=20000)

class PostmanFleet:
    def __init__(self):
        self.active_mailboxes = set()
        self.postman_signals = {}
        self.fleet_lock = threading.Lock()

    def reset_for_next_round(self):
        with code_pool_lock:
            global_code_pool.clear()

    def clear_fleet(self):
        with self.fleet_lock:
            for master_email, stop_event in self.postman_signals.items():
                stop_event.set()

            self.postman_signals.clear()
            self.active_mailboxes.clear()
            print(f"[{cfg.ts()}] [INFO] 🛑 邮局总管已下达停工令，本轮所有邮递员已下班。")

    def add_mailbox_listener(self, ms_service, master_mailbox):
        master_email = master_mailbox.get('master_email') or master_mailbox.get('email')
        from utils.email_providers.mail_service import mask_email

        with self.fleet_lock:
            if master_email in self.postman_signals:
                return

            stop_event = threading.Event()
            self.postman_signals[master_email] = stop_event

        t = threading.Thread(
            target=self._exclusive_postman_worker,
            args=(ms_service, master_mailbox, stop_event),
            daemon=True
        )
        t.start()
        print(f"[{cfg.ts()}] [INFO] 📮 派发新邮递员！开始专属监听: {mask_email(master_email)}")

    def _exclusive_postman_worker(self, ms_service, master_mailbox, stop_event):
        master_email = master_mailbox.get('master_email') or master_mailbox.get('email')
        while not getattr(cfg, 'GLOBAL_STOP', False) and not stop_event.is_set():
            try:
                messages = ms_service.fetch_openai_messages(master_mailbox)

                for m in messages:
                    msg_id = m.get('id')

                    if not msg_id or msg_id in processed_msg_ids:
                        continue

                    processed_msg_ids.add(msg_id)

                    recs = [r.get('emailAddress', {}).get('address', '').lower() for r in m.get('toRecipients', [])]
                    body = m.get('body', {}).get('content', '')
                    subject = m.get('subject', '')
                    code = None

                    new_format = re.findall(r"enter this code:\s*(\d{6})", body, re.I)
                    if not new_format:
                        new_format = re.findall(r"verification code to continue:\s*(\d{6})", body, re.I)
                    if not new_format:
                        new_format = re.findall(r"输入此(?:临时)?验证码以继续[：:]\s*(\d{6})", body, re.I)

                    if new_format:
                        code = new_format[-1]
                    else:
                        direct = re.findall(r"Your (?:ChatGPT|OpenAI) code is (\d{6})", body, re.I)
                        if direct:
                            code = direct[-1]
                        else:
                            subject_lower = subject.lower()
                            body_lower = body.lower()
                            if "chatgpt" in subject_lower or "openai" in subject_lower or "chatgpt" in body_lower or "openai" in body_lower:
                                generic = re.findall(r"(?<!\d)(\d{6})(?!\d)", body)
                                if generic:
                                    code = generic[-1]

                    if code:
                        with code_pool_lock:
                            for alias in recs:
                                # if alias not in global_code_pool:
                                global_code_pool[alias] = code
            except Exception as e:
                from utils.email_providers.mail_service import mask_email
                print(f"[{cfg.ts()}] [WARNING] 邮递员 ({mask_email(master_email)}) 遭遇阻碍: {e}")
                time.sleep(1)

            for _ in range(4):
                if getattr(cfg, 'GLOBAL_STOP', False) or stop_event.is_set():
                    break
                time.sleep(0.1)
        from utils.email_providers.mail_service import mask_email
        print(f"[{cfg.ts()}] [INFO] 🛑 ({mask_email(master_email)}) 的专属邮递员已下班，屏幕前的你下班了吗？。")


global_postman_fleet = PostmanFleet()

def wait_for_code(target_email, timeout=60):
    target_email = target_email.lower().strip()
    start_time = time.time()
    while time.time() - start_time < timeout:
        with code_pool_lock:
            if target_email in global_code_pool:
                code = global_code_pool.pop(target_email)
                from utils.email_providers.mail_service import mask_email
                print(f"[{cfg.ts()}] [SUCCESS] 🎉 ({mask_email(target_email)}) 极速领到验证码: {code}")
                return code

        time.sleep(0.1)

    return ""
