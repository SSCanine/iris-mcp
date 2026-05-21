"""App launcher: start apps from apps.yaml and resolve their windows."""
from __future__ import annotations
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

import yaml

from iris.spatial import enumerate_windows, match_window, _make_window_info, get_monitor_for_window


APPS_YAML = Path(__file__).parent.parent / "apps.yaml"

DEFAULT_APPS = {
    "obs": {
        "launch": "C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe",
        "match": {"process": "obs64.exe", "title_contains": "OBS"},
    },
    "chrome": {
        "launch": "shell:start chrome",
        "match": {"process": "chrome.exe"},
    },
    "edge": {
        "launch": "shell:start msedge",
        "match": {"process": "msedge.exe"},
    },
    "explorer": {
        "launch": "explorer.exe",
        "match": {"process": "explorer.exe", "class": "CabinetWClass"},
    },
    "vscode": {
        "launch": "shell:start code",
        "match": {"process": "Code.exe"},
    },
    "notepad": {
        "launch": "notepad.exe",
        "match": {"process": "notepad.exe", "title_contains": "Notepad"},
    },
}


def load_apps() -> dict:
    if APPS_YAML.exists():
        try:
            with APPS_YAML.open("r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
            merged = dict(DEFAULT_APPS)
            merged.update(user_cfg)
            return merged
        except Exception:
            pass
    return DEFAULT_APPS


def write_default_apps_yaml() -> None:
    if APPS_YAML.exists():
        return
    APPS_YAML.write_text(
        "# Iris app registry. Override or add entries here.\n"
        + yaml.safe_dump(DEFAULT_APPS, sort_keys=False),
        encoding="utf-8",
    )


def launch(app_name: str, *, wait_seconds: float = 5.0) -> dict:
    apps = load_apps()
    cfg = apps.get(app_name)
    if cfg is None:
        return {"ok": False, "error": f"unknown_app:{app_name}", "available": list(apps.keys())}
    launch_spec = cfg["launch"]
    match_spec = cfg.get("match", {})
    # Snapshot existing windows matching the spec so we know which is NEW
    pre = {w.hwnd for w in match_window(match_spec)}
    # Start the process
    try:
        if launch_spec.startswith("shell:"):
            cmd = launch_spec[len("shell:"):]
            subprocess.Popen(cmd, shell=True)
        else:
            subprocess.Popen(launch_spec)
    except Exception as e:
        return {"ok": False, "error": f"spawn_failed:{e}"}
    # Poll for new window
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        wins = match_window(match_spec)
        new = [w for w in wins if w.hwnd not in pre]
        if new:
            w = new[0]
            monitor = get_monitor_for_window(w.bounds)
            return {
                "ok": True,
                "app": app_name,
                "hwnd": w.hwnd,
                "pid": w.pid,
                "exe": w.exe_name,
                "title": w.title,
                "bounds": w.bounds.to_dict(),
                "monitor": monitor,
            }
        time.sleep(0.1)
    return {"ok": False, "error": "window_did_not_appear", "match_spec": match_spec}


def list_apps() -> dict:
    return {"apps": load_apps()}
