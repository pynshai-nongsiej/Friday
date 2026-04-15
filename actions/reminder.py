# actions/reminder.py

import subprocess
import os
import sys
import json
import platform
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path


def _reminders_store_path() -> Path:
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "memory" / "reminders.json"


def list_reminders() -> list[dict]:
    """Load all stored reminders."""
    path = _reminders_store_path()
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def delete_reminder(task_name: str = None, message: str = None) -> str:
    """Delete a reminder by task_name or message match."""
    path = _reminders_store_path()
    if not path.exists():
        return "No reminders found."

    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return "No reminders found."
    except Exception:
        return "Unable to load reminders."

    original_len = len(records)

    if task_name:
        records = [r for r in records if r.get("task_name") != task_name]
    elif message:
        records = [
            r for r in records if message.lower() not in r.get("message", "").lower()
        ]

    if len(records) == original_len:
        return "Reminder not found."

    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return "Reminder deleted."


def _save_reminder_record(target_dt: datetime, message: str, task_name: str) -> None:
    path = _reminders_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                records = loaded
        except Exception:
            records = []

    records.append(
        {
            "task_name": task_name,
            "message": message,
            "when": target_dt.strftime("%Y-%m-%d %H:%M"),
            "status": "scheduled",
        }
    )
    records = records[-50:]
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _notify_user(message: str) -> None:
    system = platform.system()

    if system == "Darwin":
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "MARK Reminder"',
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
        try:
            subprocess.run(["say", message], capture_output=True, timeout=10)
        except Exception:
            pass

    elif system == "Windows":
        try:
            from win10toast import ToastNotifier

            ToastNotifier().show_toast(
                "MARK Reminder", message, duration=15, threaded=False
            )
        except Exception:
            try:
                subprocess.run(["msg", "*", "/TIME:30", message], shell=True)
            except Exception:
                pass
        try:
            import winsound

            for freq in [800, 1000, 1200]:
                winsound.Beep(freq, 200)
                time.sleep(0.1)
        except Exception:
            pass
    else:
        try:
            subprocess.run(
                ["notify-send", "-u", "normal", "MARK Reminder", message],
                capture_output=True,
            )
        except Exception:
            pass


def _run_reminder_at_time(target_dt: datetime, message: str, task_name: str) -> None:
    wait_seconds = (target_dt - datetime.now()).total_seconds()
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _notify_user(message)
    _save_reminder_record(target_dt, message, task_name)


def reminder(
    parameters: dict, response: str | None = None, player=None, session_memory=None
) -> str:
    """
    Sets a timed reminder using Python threading or at/scheduler.

    parameters:
        - date    (str) YYYY-MM-DD
        - time    (str) HH:MM
        - message (str)

    Returns a result string — Live API voices it automatically.
    No edge_speak needed.
    """

    date_str = parameters.get("date")
    time_str = parameters.get("time")
    message = parameters.get("message", "Reminder")

    if not date_str or not time_str:
        return "I need both a date and a time to set a reminder."

    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")

    except ValueError:
        try:
            target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return "I couldn't understand that date or time format. Use YYYY-MM-DD and HH:MM."

    if target_dt <= datetime.now():
        return "That time is already in the past."

    task_name = f"MARKReminder_{target_dt.strftime('%Y%m%d_%H%M')}"
    safe_message = message.replace('"', "").replace("'", "").strip()[:200]

    wait_seconds = (target_dt - datetime.now()).total_seconds()

    if wait_seconds > 0 and wait_seconds < 604800:
        daemon = threading.Thread(
            target=_run_reminder_at_time,
            args=(target_dt, safe_message, task_name),
            daemon=True,
        )
        daemon.start()

    elif platform.system() == "Darwin":
        at_time = target_dt.strftime("%H:%M %Y-%m-%d")
        script_path = Path(_reminders_store_path()).parent / f"{task_name}.sh"
        script_path.write_text(
            f'#!/bin/bash\nosascript -e \'display notification "{safe_message}" with title "MARK Reminder"\'\nsay "{safe_message}"\n',
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        try:
            subprocess.run(
                ["at", at_time],
                input=f"'{script_path}'",
                shell=True,
                capture_output=True,
                text=True,
            )
            script_path.unlink()
        except Exception as e:
            print(f"[Reminder] at command failed: {e}")
            daemon = threading.Thread(
                target=_run_reminder_at_time,
                args=(target_dt, safe_message, task_name),
                daemon=True,
            )
            daemon.start()

    elif platform.system() == "Windows":
        python_exe = sys.executable
        if python_exe.lower().endswith("python.exe"):
            pythonw = python_exe.replace("python.exe", "pythonw.exe")
            if os.path.exists(pythonw):
                python_exe = pythonw

        temp_dir = os.environ.get("TEMP", "C:\\Temp")
        notify_script = os.path.join(temp_dir, f"{task_name}.pyw")
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        script_code = f'''import sys, os, time
sys.path.insert(0, r"{project_root}")

try:
    import winsound
    for freq in [800, 1000, 1200]:
        winsound.Beep(freq, 200)
        time.sleep(0.1)
except Exception:
    pass

try:
    from win10toast import ToastNotifier
    ToastNotifier().show_toast(
        "MARK Reminder",
        "{safe_message}",
        duration=15,
        threaded=False
    )
except Exception:
    try:
        import subprocess
        subprocess.run(["msg", "*", "/TIME:30", "{safe_message}"], shell=True)
    except Exception:
        pass

time.sleep(3)
try:
    os.remove(__file__)
except Exception:
    pass
'''
        with open(notify_script, "w", encoding="utf-8") as f:
            f.write(script_code)

        xml_content = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>MARK Reminder: {safe_message}</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{target_dt.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Actions>
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>"{notify_script}"</Arguments>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Principals>
    <Principal>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
</Task>'''

        xml_path = os.path.join(temp_dir, f"{task_name}.xml")
        with open(xml_path, "w", encoding="utf-16") as f:
            f.write(xml_content)

        result = subprocess.run(
            f'schtasks /Create /TN "{task_name}" /XML "{xml_path}" /F',
            shell=True,
            capture_output=True,
            text=True,
        )

        try:
            os.remove(xml_path)
        except Exception:
            pass

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"[Reminder] ❌ schtasks failed: {err}")
            try:
                os.remove(notify_script)
            except Exception:
                pass
            return "I couldn't schedule the reminder due to a system error."
    else:
        daemon = threading.Thread(
            target=_run_reminder_at_time,
            args=(target_dt, safe_message, task_name),
            daemon=True,
        )
        daemon.start()

    if player:
        player.write_log(f"[reminder] set for {date_str} {time_str}")
        if hasattr(player, "add_reminder"):
            player.add_reminder(
                message=safe_message,
                when_text=target_dt.strftime("%b %d, %Y  %I:%M %p"),
                status="scheduled",
            )
    _save_reminder_record(target_dt, safe_message, task_name)

    return f"Reminder set for {target_dt.strftime('%B %d at %I:%M %p')}."
