"""
Uploads titibet.db to the Fly.io volume in chunks.
Compresses with gzip first (330 MB → ~50 MB).
Run from the project root: python upload_db.py
"""
import gzip
import io
import subprocess
import sys
import time
import tempfile
from pathlib import Path

DB_PATH = Path(__file__).parent / "backend" / "titibet.db"
APP = "titibet"
MACHINE_ID = "9185dd66f652e8"
REMOTE = "/data/titibet.db"
CHUNK_SIZE = 20 * 1024 * 1024  # 20 MB per chunk
MAX_RETRIES = 5

if not DB_PATH.exists():
    sys.exit(f"ERROR: {DB_PATH} not found")

raw = DB_PATH.stat().st_size
print(f"Original DB: {raw / 1024**2:.1f} MB — compressing...", flush=True)
buf = io.BytesIO()
with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
    gz.write(DB_PATH.read_bytes())
data = buf.getvalue()
print(f"Compressed:  {len(data) / 1024**2:.1f} MB")


def ssh(cmd):
    r = subprocess.run(
        ["flyctl", "ssh", "console", "--app", APP, "-C", cmd],
        capture_output=True, text=True,
    )
    return r.stdout + r.stderr


def sftp_put(local: Path, remote: str) -> bool:
    r = subprocess.run(
        ["flyctl", "ssh", "sftp", "put", "--app", APP, str(local), remote],
    )
    return r.returncode == 0


# Start machine and wait for SSH
print("Starting machine...", flush=True)
subprocess.run(["flyctl", "machine", "start", MACHINE_ID], capture_output=True)
print("Waiting for SSH", end="", flush=True)
for _ in range(40):
    out = ssh("echo ready")
    if "ready" in out:
        print(" OK")
        break
    print(".", end="", flush=True)
    time.sleep(5)
else:
    sys.exit("\nERROR: machine never became SSH-ready")

# Always wipe old parts before starting
print("Clearing old part files from VM...")
ssh("find /data -name 'titibet.db.part*' -delete 2>/dev/null; echo cleared")
time.sleep(2)

# Upload chunks
n = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE
print(f"Uploading {n} chunks...", flush=True)

with tempfile.TemporaryDirectory() as tmp:
    for i in range(n):
        name = f"titibet.db.part{i:03d}"
        remote_path = f"/data/{name}"
        start = i * CHUNK_SIZE
        chunk_data = data[start : start + CHUNK_SIZE]
        local_path = Path(tmp) / name
        local_path.write_bytes(chunk_data)

        for attempt in range(1, MAX_RETRIES + 1):
            # Remove the remote part if it somehow already exists
            ssh(f"rm -f {remote_path}")
            time.sleep(1)
            print(f"  Chunk {i+1}/{n} ({len(chunk_data)/1024**2:.1f} MB) attempt {attempt}...",
                  end=" ", flush=True)
            if sftp_put(local_path, remote_path):
                print("OK")
                break
            print("failed, retrying...")
            time.sleep(10)
        else:
            sys.exit(f"ERROR: chunk {i+1} failed after {MAX_RETRIES} attempts. Re-run to retry.")

# Verify parts exist on VM
print("Verifying parts on VM...")
out = ssh("ls -la /data/titibet.db.part* 2>&1")
print(out.strip())

# Concatenate and decompress using Python on the VM (avoids shell escaping issues)
print("Concatenating and decompressing on VM...")
py = (
    "import gzip, pathlib; "
    "parts = sorted(pathlib.Path('/data').glob('titibet.db.part*')); "
    "print(f'Found {len(parts)} parts'); "
    "data = b''.join(p.read_bytes() for p in parts); "
    "pathlib.Path('/data/titibet.db').write_bytes(gzip.decompress(data)); "
    "[p.unlink() for p in parts]; "
    "print('done')"
)
out = ssh(f"python3 -c \"{py}\"")
print(" VM:", out.strip())

# Verify
print("Verifying integrity...")
out = ssh(
    f"python3 -c \"import sqlite3; c=sqlite3.connect('{REMOTE}'); "
    f"print(c.execute('PRAGMA integrity_check').fetchone()[0])\""
)
check = out.strip().splitlines()[-1] if out.strip() else ""
print(f"Integrity check: {check}")
if check != "ok":
    sys.exit("ERROR: integrity check failed — do not restart")

print("Restarting machine...")
subprocess.run(["flyctl", "machine", "restart", MACHINE_ID])
print("Done! Visit https://titibet.fly.dev")
