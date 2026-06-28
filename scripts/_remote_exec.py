import os
import sys
import paramiko

host = os.environ["RHOST"]
user = os.environ["RUSER"]
pwd = os.environ["RPASS"]
cmd = sys.stdin.read()

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=pwd, timeout=20, look_for_keys=False, allow_agent=False)
chan = c.get_transport().open_session()
chan.exec_command(cmd)
out = b""
err = b""
while True:
    if chan.recv_ready():
        out += chan.recv(65536)
    if chan.recv_stderr_ready():
        err += chan.recv_stderr(65536)
    if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
        break
rc = chan.recv_exit_status()
# drain
import time
time.sleep(0.2)
while chan.recv_ready():
    out += chan.recv(65536)
while chan.recv_stderr_ready():
    err += chan.recv_stderr(65536)
sys.stdout.write(out.decode("utf-8", "replace"))
if err:
    sys.stderr.write(err.decode("utf-8", "replace"))
c.close()
sys.exit(rc)
