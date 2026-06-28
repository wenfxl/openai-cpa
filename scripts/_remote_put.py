import os
import sys
import paramiko

host = os.environ["RHOST"]
user = os.environ["RUSER"]
pwd = os.environ["RPASS"]
local = sys.argv[1]
remote = sys.argv[2]

t = paramiko.Transport((host, 22))
t.connect(username=user, password=pwd)
sftp = paramiko.SFTPClient.from_transport(t)


def cb(done, total):
    pct = (done / total * 100) if total else 0
    sys.stdout.write(f"\rupload {done}/{total} ({pct:.0f}%)")
    sys.stdout.flush()


sftp.put(local, remote, callback=cb)
print()
st = sftp.stat(remote)
print(f"uploaded size={st.st_size}")
sftp.close()
t.close()
