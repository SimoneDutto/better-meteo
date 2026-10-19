"""Manage this project's background Node server on Linux. Never contacts providers."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'data'
STATE = RUNTIME/'dashboard.pid.json'
LOG = RUNTIME/'dashboard.log'

def identity(pid):
    """Linux start ticks protect against a stale PID file killing a reused PID."""
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        if stat[0]=='Z': return None
        return stat[19]  # field 22: starttime; fields here begin at field 3
    except (FileNotFoundError,ProcessLookupError):
        return None

def running():
    try:
        state = json.loads(STATE.read_text())
        pid = int(state['pid'])
        if pid>1 and isinstance(state.get('start_ticks'),str) and identity(pid)==state['start_ticks']:
            return state
    except (FileNotFoundError,ValueError,KeyError,TypeError):
        pass
    return None

def stop():
    state = running()
    if state is None:
        STATE.unlink(missing_ok=True)
        print('No managed dashboard is running.')
        return
    pid=state['pid']
    os.kill(pid,signal.SIGTERM)
    for _ in range(100):
        if identity(pid)!=state['start_ticks']:
            STATE.unlink(missing_ok=True)
            print(f'Dashboard stopped (PID {pid}).')
            return
        time.sleep(.1)
    raise RuntimeError('Dashboard did not stop within 10 seconds; no replacement started. Check make logs.')

def start():
    previous=running()
    if previous:
        print(f'Dashboard already running at {previous["url"]} (PID {previous["pid"]}). Use make restart to replace it.')
        return
    entry=ROOT/'dist/server/entry.mjs'
    if not entry.exists(): raise RuntimeError('No build found. Run make build first.')
    if not Path('/proc/self/stat').exists(): raise RuntimeError('This background process helper requires Linux; run the Node entrypoint directly on other systems.')
    host=os.environ.get('HOST','127.0.0.1')
    port=int(os.environ.get('PORT','4321'))
    if not 1<=port<=65535: raise ValueError('PORT must be between 1 and 65535')
    environment={**os.environ,'HOST':host,'PORT':str(port)}
    environment.pop('ASTRO_NODE_AUTOSTART',None)
    with LOG.open('ab') as log:
        process=subprocess.Popen([os.environ.get('NODE','node'),str(entry)],cwd=ROOT,
            env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    ticks=identity(process.pid)
    url=f'http://{f"[{host}]" if ":" in host else host}:{port}'
    STATE.write_text(json.dumps({'pid':process.pid,'start_ticks':ticks,'url':url}))
    # Astro can exit with code 0 on a bind failure, so any early exit is a failure.
    for _ in range(10):
        time.sleep(.1)
        if process.poll() is not None:
            STATE.unlink(missing_ok=True)
            raise RuntimeError(f'Dashboard exited during startup. See {LOG}. If another server is using port {port}, stop it first or choose PORT=another-port.')
    print(f'Dashboard started at {url} (PID {process.pid}). Logs: {LOG}')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['start','stop','restart','status'])
    command=parser.parse_args().command
    RUNTIME.mkdir(parents=True,exist_ok=True)
    with (RUNTIME/'dashboard.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if command=='restart':
            stop()
            start()
        elif command=='start': start()
        elif command=='stop': stop()
        else:
            state=running()
            print(f'Dashboard running at {state["url"]} (PID {state["pid"]}).' if state else 'No managed dashboard is running.')

if __name__=='__main__':
    try: main()
    except (OSError,ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
