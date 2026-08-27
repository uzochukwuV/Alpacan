#!/usr/bin/env python3
"""Double-fork daemon launcher for scripts/keeper.sh.

The sandbox may reap background children tied to a finished shell session.
Double-forking + setsid detaches the keeper into its own session so it is
reparented to init and survives.
"""
import os
import sys


BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
PID_PATH = os.path.join(BASE, "run_data", "keeper.pid")


def daemonize():
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)


def main():
    daemonize()
    os.makedirs(os.path.dirname(PID_PATH), exist_ok=True)
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    os.chdir(BASE)
    os.execv("/bin/bash", ["/bin/bash", os.path.join(BASE, "scripts", "keeper.sh")])


if __name__ == "__main__":
    main()