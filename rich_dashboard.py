import threading
import time
import queue
from datetime import datetime
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.align import Align
import sys

class RichDashboard:
    def __init__(self, refresh_per_second=4):
        self.console = Console()
        self.log_queue = queue.Queue()
        self.logs = []  # list of (timestamp, message)
        self.max_logs = 1000  # Increased to keep more logs
        self.refresh_per_second = refresh_per_second
        self._thread = None
        self._stop_event = threading.Event()
        self._live = None
        self._status_info = {
            "status": "Initializing...",
            "personality": "unknown",
            "uptime": "00:00:00",
            "logs_count": 0
        }
        self._start_time = datetime.now()

    def start(self):
        """Start the dashboard in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the dashboard."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._live:
            self._live.stop()

    def log(self, message):
        """Add a log message (thread-safe)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put((timestamp, message))

    def update_status(self, key, value):
        """Update status information."""
        self._status_info[key] = value

    def _run(self):
        """Main dashboard loop."""
        try:
            with Live(self._generate_layout(), refresh_per_second=self.refresh_per_second, console=self.console) as live:
                self._live = live
                while not self._stop_event.is_set():
                    # Process incoming log messages
                    try:
                        while True:
                            timestamp, msg = self.log_queue.get_nowait()
                            self.logs.append((timestamp, msg))
                            # Keep only the last max_logs entries
                            if len(self.logs) > self.max_logs:
                                self.logs = self.logs[-self.max_logs:]
                    except queue.Empty:
                        pass

                    # Update status info
                    self._status_info["logs_count"] = len(self.logs)
                    uptime = datetime.now() - self._start_time
                    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    self._status_info["uptime"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                    # Update the display
                    live.update(self._generate_layout())
                    time.sleep(0.05)  # Small sleep to prevent excessive CPU usage
        except Exception as e:
            print(f"Rich dashboard error: {e}", file=sys.stderr)

    def _generate_layout(self):
        """Generate the layout for the dashboard."""
        layout = Layout()

        # Header
        header = Table.grid(expand=True)
        header.add_column(justify="left", ratio=2)
        header.add_column(justify="right", ratio=1)
        header.add_row(
            Text("FRIDAY Terminal Dashboard", style="bold cyan"),
            Text(f"Uptime: {self._status_info['uptime']}", style="dim")
        )

        # Logs panel
        log_table = Table(show_header=False, box=None, padding=(0, 1))
        log_table.add_column("Time", style="dim", width=8)
        log_table.add_column("Message", overflow="fold")

        # Add recent logs (last 30)
        for timestamp, msg in self.logs[-30:]:
            log_table.add_row(timestamp, msg)

        logs_panel = Panel(
            log_table,
            title="[bold]Recent Logs[/bold]",
            border_style="blue",
            padding=(1, 2)
        )

        # Status panel
        status_table = Table(show_header=False, box=None)
        status_table.add_column("Label", style="bold cyan")
        status_table.add_column("Value")
        status_table.add_row("Status", self._status_info["status"])
        status_table.add_row("Personality", self._status_info["personality"])
        status_table.add_row("Total Logs", str(self._status_info["logs_count"]))

        status_panel = Panel(
            status_table,
            title="[bold]System Status[/bold]",
            border_style="green",
            padding=(1, 2)
        )

        # Combine into layout
        layout.split_column(
            Layout(Header(header), size=3),
            Layout(logs_panel, ratio=3),
            Layout(status_panel, size=8)
        )

        return layout

class Header:
    """Rich renderable for the header."""
    def __init__(self, table):
        self.table = table

    def __rich_console__(self, console, options):
        yield self.table

# Global dashboard instance
_dashboard = None

def get_dashboard():
    global _dashboard
    if _dashboard is None:
        try:
            _dashboard = RichDashboard()
        except Exception:
            _dashboard = None
    return _dashboard

def start_dashboard():
    dashboard = get_dashboard()
    if dashboard:
        dashboard.start()
    return dashboard

def stop_dashboard():
    global _dashboard
    if _dashboard:
        _dashboard.stop()
        _dashboard = None

def log_message(message):
    """Log a message to the dashboard if it exists."""
    global _dashboard
    if _dashboard is not None:
        _dashboard.log(message)