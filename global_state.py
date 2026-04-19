import threading
from collections import deque
from fastapi import Header, HTTPException
from utils import core_engine
import utils.config as cfg

VALID_TOKENS = set()
CLUSTER_NODES = {}
NODE_COMMANDS = {}
cluster_lock = threading.Lock()
log_history = deque(maxlen=cfg.MAX_LOG_LINES)
worker_status: dict = {}
engine = core_engine.RegEngine()


def append_log(msg: str):
    log_history.append(msg)


async def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效凭证")
    token = authorization.split(" ")[1]
    if token not in VALID_TOKENS:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return token