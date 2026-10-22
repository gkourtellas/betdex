"""Weekly backup for the BetDEX bot.

It snapshots what the bot cannot afford to lose:

  bets.db         - every bet ever placed (the record used for the reports)
  strategies.json - the strategy setup itself
  state/          - the live progress of each strategy (the open ladders)

Each snapshot is written to data/backups/ and copied to the NAS
(/mnt/nas/DB_Backups/BetDEX). The old copies are dropped, the newest
KEEP of each are kept.

The local copies protect against a corrupted or emptied file. The NAS copies
protect against a dead disk.

If anything goes wrong, a Telegram message says so.
"""
import datetime as dt
import os
import shutil
import sqlite3
import sys
import tarfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
LOCAL_DIR = ROOT / "data" / "backups"
NAS_DIR = "/mnt/nas/DB_Backups/BetDEX"
KEEP = 8              # how many weekly copies of each file to keep
API = "https://api.telegram.org/bot{token}/sendMessage"


# ---- what gets backed up ----------------------------------------------------

def snapshot_db(src: Path, dest: Path) -> Path:
    """A consistent copy of a live database, taken with SQLite's own backup.

    Safe to run while the bot is writing to the same file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(src)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return dest


def snapshot_file(src: Path, dest: Path) -> Path:
    """A copy of a plain file (strategies.json)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


def snapshot_dir(src: Path, dest: Path) -> Path:
    """A copy of a folder, packed into one .tar.gz file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(src, arcname=src.name)
    return dest


def artifacts(day: str, out_dir: Path) -> list:
    """The list of (prefix, full path) backups made for this day."""
    made = []
    made.append(("bets", snapshot_db(CONFIG / "bets.db", out_dir / f"bets-{day}.db")))
    made.append(("strategies", snapshot_file(CONFIG / "strategies.json", out_dir / f"strategies-{day}.json")))
    made.append(("state", snapshot_dir(CONFIG / "state", out_dir / f"state-{day}.tar.gz")))
    return made


# ---- keeping the copies -----------------------------------------------------

def rotate(out_dir: Path, prefixes: list, keep: int = KEEP) -> list:
    """Keep the newest `keep` copies of each file, drop the older ones."""
    gone = []
    for prefix in prefixes:
        files = sorted(out_dir.glob(prefix + "-*"))
        for f in (files[:-keep] if keep > 0 else files):
            f.unlink()
            gone.append(f.name)
    return gone


def copy_to_nas(files: list, nas_dir: str, keep: int = KEEP, log=print) -> list:
    """Copy the finished snapshots to the NAS. Raises if the share is not there."""
    if not nas_dir:
        raise RuntimeError("no NAS folder set")
    parent = Path(nas_dir).parent
    if not parent.is_dir():
        raise RuntimeError(f"NAS not mounted: {parent}")
    Path(nas_dir).mkdir(parents=True, exist_ok=True)
    sent = []
    for f in files:
        dest = Path(nas_dir) / Path(f).name
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(f, tmp)
        os.replace(tmp, dest)       # the copy appears complete, or not at all
        sent.append(str(dest))
        log(f"copied to NAS: {dest}")
    for prefix in ("bets", "strategies", "state"):
        old = sorted(Path(nas_dir).glob(prefix + "-*"))
        for f in (old[:-keep] if keep > 0 else old):
            f.unlink()
    return sent


# ---- Telegram ---------------------------------------------------------------

def read_env(path: Path = ROOT / ".env") -> dict:
    """Reads the .env file (key=value lines, # comments ignored)."""
    env = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def notify(text: str, env: dict) -> bool:
    """Sends the message to the alerts chat. False if Telegram is not set up."""
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        r = requests.post(API.format(token=token),
                          data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"},
                          timeout=20)
        if r.status_code != 200:
            print(f"Telegram refused the message: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"Telegram could not be reached: {e}")
        return False


# ---- the run ----------------------------------------------------------------

def run(keep: int = KEEP, log=print) -> dict:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()

    made = artifacts(day, LOCAL_DIR)
    for _, f in made:
        log(f"wrote {f} ({os.path.getsize(f) // 1024} KB)")

    gone = rotate(LOCAL_DIR, ["bets", "strategies", "state"], keep)
    if gone:
        log("removed old copies: " + ", ".join(gone))

    sent = copy_to_nas([f for _, f in made], NAS_DIR, keep, log)
    return {"files": [str(f) for _, f in made], "removed": gone, "nas": sent}


def main() -> int:
    keep = KEEP
    if "--keep" in sys.argv:
        keep = int(sys.argv[sys.argv.index("--keep") + 1])
    try:
        result = run(keep)
    except Exception as e:
        message = (f"❌ BetDEX backup FAILED\n"
                   f"{type(e).__name__}: {e}\n"
                   f"Local copies: {LOCAL_DIR}\n"
                   f"NAS copies: {NAS_DIR}")
        print(message)
        notify(message, read_env())
        return 1
    print(f"Backup done: {len(result['files'])} files, {len(result['nas'])} on the NAS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
