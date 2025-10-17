# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025 Nucleon Power, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import sys
import threading
import time
import queue
import csv
import re
from datetime import datetime, timezone, timedelta
from collections import deque

import serial
import serial.tools.list_ports

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


APP_TITLE = "Dragonfly Neutron Detector Control - Rev.01 - Copyright (c) 2025 Nucleon Power, Inc."
DEFAULT_BAUD = 115200
SERIES_LEFT = ["V"]     # Voltage on left axis (Volts)
SERIES_RIGHT = ["I", "C"]  # Current (uA) & Counts/sec on right axis


def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def _is_number(s):
    try:
        float(s)
        return True
    except Exception:
        return False


def _parse_float(token):
    """Parse float or a simple 'a/b' expression (e.g., '1.0/60')."""
    token = token.strip()
    try:
        return float(token)
    except Exception:
        if "/" in token:
            num, denom = token.split("/", 1)
            try:
                return float(num) / float(denom)
            except Exception:
                return None
        return None


def _parse_timestamp(s):
    # Try a few formats listed in MegunoLink docs
    for fmt in ("%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%H:%M:%S.%f",
                "%H:%M:%S",
                "%H:%M",
                "%Y-%m-%d"):
        try:
            dt_naive = datetime.strptime(s, fmt)
            # if time-only, assume today local
            if fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
                today = datetime.now().astimezone()
                dt = today.replace(hour=dt_naive.hour, minute=dt_naive.minute,
                                   second=getattr(dt_naive, "second", 0),
                                   microsecond=getattr(dt_naive, "microsecond", 0))
            else:
                dt = dt_naive.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt
        except Exception:
            continue
    return None


class SerialReader(threading.Thread):
    def __init__(self, ser, out_queue, stop_event):
        super().__init__(daemon=True)
        self.ser = ser
        self.out = out_queue
        self.stop_event = stop_event
        self.buf = ""

    def run(self):
        self.ser.reset_input_buffer()
        while not self.stop_event.is_set():
            try:
                n = self.ser.in_waiting
                if n == 0:
                    time.sleep(0.01)
                    continue
                data = self.ser.read(n)
                try:
                    chunk = data.decode("utf-8", errors="ignore")
                except Exception:
                    chunk = data.decode("latin-1", errors="ignore")
                self.buf += chunk
                self._parse_buffer()
            except serial.SerialException as e:
                self.out.put(("error", f"Serial error: {e}"))
                break
            except Exception as e:
                self.out.put(("error", f"Reader exception: {e}"))

    def _parse_buffer(self):
        # Extract {...} messages; keep remainder in buffer
        while True:
            start = self.buf.find("{")
            if start == -1:
                if len(self.buf) > 10000:
                    self.buf = self.buf[-10000:]
                return
            end = self.buf.find("}", start + 1)
            if end == -1:
                break
            message = self.buf[start + 1 : end]
            self.buf = self.buf[end + 1 :]
            self._handle_brace_message(message)

        # Fallback: parse newline-delimited "V:123 I:45 C:6" style lines
        while True:
            nl = self.buf.find("\n")
            if nl == -1:
                break
            line = self.buf[:nl].strip()
            self.buf = self.buf[nl + 1:]
            if not line:
                continue
            self._handle_text_line(line)

    def _handle_brace_message(self, msg):
        # General format: {PLOTTYPE[:channel]|COMMAND|...}
        # We support TIMEPLOT commands used by MegunoLink's TimePlot class.
        try:
            parts = msg.split("|")
            if not parts:
                return
            header = parts[0].strip()
            plot_type = header.split(":")[0].strip().upper()
            if plot_type != "TIMEPLOT":
                return

            if len(parts) < 2:
                return
            cmd = parts[1].strip().upper()

            if cmd in ("DATA", "D", "DATA-STEP", "DS"):
                self._handle_data_cmd(cmd, parts)
                return

            # XRANGE / YRANGE / Y2RANGE
            if cmd in ("XRANGE", "YRANGE", "Y2RANGE"):
                self._handle_range_cmd(cmd, parts)
                return

            # CLEAR (optional series name)
            if cmd == "CLEAR":
                series = parts[2].strip() if len(parts) >= 3 else None
                self.out.put(("clear", series))
                return

            # SET: plot/axis properties (Title, X-Label, Y-Label, Y2-Label, Y-Visible, Y2-Visible)
            if cmd == "SET":
                if len(parts) >= 3:
                    kv = parts[2].strip()
                    self.out.put(("set", kv))
                return

            # STYLE: we just ignore or could parse for completeness
            if cmd == "STYLE":
                # example: STYLE|Series name:PropertyString
                return

        except Exception as e:
            self.out.put(("error", f"Parse error for message {{{msg}}}: {e}"))

    def _handle_data_cmd(self, cmd, parts):
        # DATA/D: {TIMEPLOT|DATA|Series|T|Y} or with explicit timestamp
        # DATA-STEP/DS: {TIMEPLOT|DS|Series|T|stepSec|y1|y2|...}
        series_field = parts[2].strip() if len(parts) > 2 else None
        if not series_field:
            return
        series = series_field.split(":")[0].strip()

        if cmd in ("DATA", "D"):
            if len(parts) < 5:
                return
            x = parts[3].strip()
            y = parts[4].strip()
            if not _is_number(y):
                return
            if x.upper() == "T":
                ts = datetime.now().astimezone()
            else:
                ts = _parse_timestamp(x) or datetime.now().astimezone()
            self.out.put(("data", series, ts, float(y)))
        else:
            # DATA-STEP
            if len(parts) < 6:
                return
            x0 = parts[3].strip()
            step = _parse_float(parts[4].strip())
            values = [float(p) for p in parts[5:] if _is_number(p)]
            if x0.upper() == "T":
                base = datetime.now().astimezone()
                if step is None:
                    step = 0
                # step is in seconds for timeplots when x0 == 'T'
                for i, y in enumerate(values):
                    ts = base + timedelta(seconds=step * i)
                    self.out.put(("data", series, ts, float(y)))
            else:
                # explicit x0 not implemented for timeplots; send first value with 'now'
                if values:
                    self.out.put(("data", series, datetime.now().astimezone(), float(values[0])))

    def _handle_range_cmd(self, cmd, parts):
        # XRANGE: {TIMEPLOT|XRANGE|T|hours}
        # YRANGE: {TIMEPLOT|YRANGE|low|high}
        # Y2RANGE: {TIMEPLOT|Y2RANGE|low|high}
        try:
            if cmd == "XRANGE" and len(parts) >= 4:
                unit = parts[2].strip().upper()
                span = _parse_float(parts[3].strip())
                if unit == "T" and span is not None:
                    self.out.put(("xrange_hours", span))
            elif cmd == "YRANGE" and len(parts) >= 4:
                low = _parse_float(parts[2].strip())
                high = _parse_float(parts[3].strip())
                if low is not None and high is not None:
                    self.out.put(("yrange_left", (low, high)))
            elif cmd == "Y2RANGE" and len(parts) >= 4:
                low = _parse_float(parts[2].strip())
                high = _parse_float(parts[3].strip())
                if low is not None and high is not None:
                    self.out.put(("yrange_right", (low, high)))
        except Exception as e:
            self.out.put(("error", f"Range parse error: {e}"))

    def _handle_text_line(self, line):
        tokens = dict(re.findall(r"([VIC])\s*[:=]\s*([-+]?\d*\.?\d+)", line, flags=re.I))
        if tokens:
            ts = datetime.now().astimezone()
            for k, v in tokens.items():
                try:
                    self.out.put(("data", k.upper(), ts, float(v)))
                except ValueError:
                    pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x760")

        self.ser = None
        self.reader = None
        self.reader_stop = threading.Event()
        self.msg_queue = queue.Queue()

        # data storage
        self.all_data = []  # list of rows {timestamp, epoch_s, V, I, C}
        self.latest = {"V": None, "I": None, "C": None}
        self.window_data = {k: deque() for k in ["V", "I", "C"]}  # (ts, value)
        self.stats_interval_sec = tk.IntVar(value=60)
        self.hv_value = tk.IntVar(value=0)
        self.rolling_seconds = 60  # default; can be overridden by XRANGE

        self._build_ui()
        self._schedule_updates()

    # ------------------- UI -------------------
    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        # Serial controls
        serial_frame = ttk.LabelFrame(top, text="Serial Connection")
        serial_frame.pack(side="left", padx=8, pady=8, fill="x")

        self.port_var = tk.StringVar()
        self.ports_combo = ttk.Combobox(serial_frame, textvariable=self.port_var, width=28, state="readonly")
        self.ports_combo.pack(side="left", padx=4)
        ttk.Button(serial_frame, text="Refresh", command=self.refresh_ports).pack(side="left", padx=4)
        ttk.Button(serial_frame, text="Connect", command=self.connect).pack(side="left", padx=4)
        ttk.Button(serial_frame, text="Disconnect", command=self.disconnect).pack(side="left", padx=4)
        self.status_label = ttk.Label(serial_frame, text="Disconnected", width=24)
        self.status_label.pack(side="left", padx=8)

        # Voltage control
        hv_frame = ttk.LabelFrame(top, text="High Voltage Setpoint (V)")
        hv_frame.pack(side="left", padx=12, pady=8)
        self.hv_spin = ttk.Spinbox(hv_frame, from_=0, to=1500, increment=1, textvariable=self.hv_value, width=8)
        self.hv_spin.pack(side="left", padx=4)
        ttk.Button(hv_frame, text="Send", command=self.send_hv).pack(side="left", padx=6)

        # Stats controls
        stats_ctrl = ttk.LabelFrame(top, text="Stats Window (s)")
        stats_ctrl.pack(side="left", padx=12, pady=8)
        self.stats_spin = ttk.Spinbox(stats_ctrl, from_=5, to=3600, increment=5,
                                      textvariable=self.stats_interval_sec, width=8, command=self.update_stats_table)
        self.stats_spin.pack(side="left", padx=4)

        ttk.Button(top, text="Export CSV", command=self.export_csv).pack(side="right", padx=8)

        # Plot area
        plot_frame = ttk.Frame(self)
        plot_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self.fig = plt.Figure(figsize=(8, 5), dpi=100)
        self.ax_left = self.fig.add_subplot(111)
        self.ax_right = self.ax_left.twinx()

        self.line_V, = self.ax_left.plot([], [], label="Voltage (V)")
        self.line_I, = self.ax_right.plot([], [], label="Current (uA)")
        self.line_C, = self.ax_right.plot([], [], label="Counts/s")

        self.ax_left.set_xlabel("Time")
        self.ax_left.set_ylabel("Voltage (V)")
        self.ax_right.set_ylabel("Current (uA) & Counts/s")

        self.ax_left.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        self.ax_left.grid(True, linestyle="--", alpha=0.3)

        self.ax_left.set_ylim(0, 2000)
        self.ax_right.set_ylim(0, 250)

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill="both", expand=True)

        # Stats table
        table_frame = ttk.LabelFrame(self, text="Summary (window = N seconds)")
        table_frame.pack(fill="x", padx=8, pady=8)

        self.table = ttk.Treeview(table_frame, columns=("series", "min", "max", "avg"), show="headings", height=4)
        for col, hdr in zip(("series","min","max","avg"), ("Series","Min","Max","Avg")):
            self.table.heading(col, text=hdr)
            self.table.column(col, width=140, anchor="center")
        self.table.pack(fill="x", padx=6, pady=6)
        for s in ["V","I","C"]:
            self.table.insert("", "end", iid=s, values=(s, "-", "-", "-"))

        # Log area
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="x", padx=8, pady=8)
        self.log_text = tk.Text(log_frame, height=6, state="disabled")
        self.log_text.pack(fill="x", padx=4, pady=4)

        # Initialize ports
        self.refresh_ports()

    # ------------------- Serial -------------------
    def refresh_ports(self):
        ports = list_serial_ports()
        self.ports_combo["values"] = ports
        if ports:
            sel = self.port_var.get()
            if sel in ports:
                self.ports_combo.set(sel)
            else:
                self.ports_combo.current(0)

    def connect(self):
        if self.ser and self.ser.is_open:
            messagebox.showinfo("Info", "Already connected.")
            return
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("Port", "Choose a serial port first.")
            return
        try:
            self.ser = serial.Serial(port, DEFAULT_BAUD, timeout=0)
            self.reader_stop.clear()
            self.reader = SerialReader(self.ser, self.msg_queue, self.reader_stop)
            self.reader.start()
            self.status_label.config(text=f"Connected @ {port}")
            self._log(f"Connected to {port} @ {DEFAULT_BAUD} baud")
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
            self.status_label.config(text="Disconnected")

    def disconnect(self):
        try:
            self.reader_stop.set()
            if self.reader:
                self.reader.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.status_label.config(text="Disconnected")
        self._log("Disconnected.")

    # ------------------- Commands -------------------
    def send_hv(self):
        if not (self.ser and self.ser.is_open):
            messagebox.showwarning("Serial", "Connect to the serial port first.")
            return
        try:
            value = int(self.hv_value.get())
        except Exception:
            messagebox.showerror("Value", "HV must be an integer (0-1500).")
            return
        value = max(0, min(1500, value))
        self.hv_value.set(value)
        # End with CRLF to match your example; MegunoLink handler uses '\r' by default
        cmd = f"!SetHV {value}\r\n".encode("ascii")
        try:
            self.ser.write(cmd)
            self._log(f"TX: {cmd!r}")
        except Exception as e:
            messagebox.showerror("Write failed", str(e))

    # ------------------- Data Handling -------------------
    def _schedule_updates(self):
        self.after(50, self._drain_queue)
        self.after(250, self._update_plot)
        self.after(1000, self.update_stats_table)

    def _drain_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                if not item:
                    break
                kind = item[0]
                if kind == "data":
                    _, series, ts, value = item
                    self._ingest_point(series, ts, value)
                elif kind == "error":
                    self._log(f"ERR: {item[1]}")
                elif kind == "xrange_hours":
                    hours = float(item[1])
                    self.rolling_seconds = max(1, int(hours * 3600))
                    self._log(f"XRANGE set to {hours} h ({self.rolling_seconds}s window)")
                elif kind == "yrange_left":
                    low, high = item[1]
                    self.ax_left.set_ylim(low, high)
                    self._log(f"YRANGE left set to {low}..{high}")
                elif kind == "yrange_right":
                    low, high = item[1]
                    self.ax_right.set_ylim(low, high)
                    self._log(f"YRANGE right set to {low}..{high}")
                elif kind == "clear":
                    series = item[1]
                    self._clear_series(series)
                elif kind == "set":
                    self._apply_set_property(item[1])
                else:
                    self._log(f"DBG: {item}")
        except queue.Empty:
            pass
        finally:
            self.after(50, self._drain_queue)

    def _apply_set_property(self, kv):
        # kv looks like "Title=...", "X-Label=...", "Y-Label=...", "Y2-Label=..."
        if "=" not in kv:
            return
        key, val = kv.split("=", 1)
        key_norm = key.strip().lower().replace(" ", "").replace("-", "")
        val = val.strip()
        if key_norm == "title":
            self.ax_left.set_title(val)
            self._log(f"SET Title -> {val}")
        elif key_norm in ("xlabel", "xlabel"):
            self.ax_left.set_xlabel(val)
            self._log(f"SET X-Label -> {val}")
        elif key_norm in ("ylabel",):
            self.ax_left.set_ylabel(val)
            self._log(f"SET Y-Label -> {val}")
        elif key_norm in ("y2label", "ylabel2"):
            self.ax_right.set_ylabel(val)
            self._log(f"SET Y2-Label -> {val}")
        elif key_norm in ("yvisible",):
            vis = val not in ("0", "false", "False")
            self.ax_left.get_yaxis().set_visible(vis)
            self._log(f"SET Y-Visible -> {vis}")
        elif key_norm in ("y2visible", "yvisible2"):
            vis = val not in ("0", "false", "False")
            self.ax_right.get_yaxis().set_visible(vis)
            self._log(f"SET Y2-Visible -> {vis}")
        # else: ignore

    def _clear_series(self, series):
        if series is None:
            for s in ["V", "I", "C"]:
                self.window_data[s].clear()
            self._log("CLEAR all series")
        else:
            s = series.split(":")[0].strip()
            if s in self.window_data:
                self.window_data[s].clear()
                self._log(f"CLEAR {s}")

    def _ingest_point(self, series, ts, value):
        if series not in ("V","I","C"):
            return
        self.latest[series] = (ts, value)
        dq = self.window_data[series]
        dq.append((ts, value))
        # prune old points for current horizon
        horizon = max(self.rolling_seconds, self.stats_interval_sec.get())
        cutoff = ts - timedelta(seconds=horizon * 1.2)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        # Save a combined row at this timestamp using latest values
        row = {
            "timestamp": ts.isoformat(),
            "epoch_s": ts.timestamp(),
            "V": self.latest["V"][1] if self.latest["V"] else "",
            "I": self.latest["I"][1] if self.latest["I"] else "",
            "C": self.latest["C"][1] if self.latest["C"] else "",
        }
        self.all_data.append(row)

    # ------------------- Plot & Stats -------------------
    def _update_plot(self):
        now = datetime.now().astimezone()
        t0 = now - timedelta(seconds=self.rolling_seconds)

        def series_xy(series_name):
            pts = [(ts, v) for (ts, v) in self.window_data[series_name] if ts >= t0]
            if not pts:
                return [], []
            xs = [mdates.date2num(ts) for ts, _ in pts]
            ys = [v for _, v in pts]
            return xs, ys

        xV, yV = series_xy("V")
        xI, yI = series_xy("I")
        xC, yC = series_xy("C")

        self.line_V.set_data(xV, yV)
        self.line_I.set_data(xI, yI)
        self.line_C.set_data(xC, yC)

        self.ax_left.set_xlim(mdates.date2num(t0), mdates.date2num(now))

        self.ax_left.legend(loc="upper left")
        self.ax_right.legend(loc="upper right")

        self.canvas.draw_idle()
        self.after(250, self._update_plot)

    def update_stats_table(self):
        window = max(1, int(self.stats_interval_sec.get()))
        cutoff = datetime.now().astimezone() - timedelta(seconds=window)
        for s in ["V","I","C"]:
            vals = [v for (ts, v) in self.window_data[s] if ts >= cutoff]
            if vals:
                mn, mx = min(vals), max(vals)
                avg = sum(vals) / len(vals)
                self.table.set(s, "min", f"{mn:.3f}")
                self.table.set(s, "max", f"{mx:.3f}")
                self.table.set(s, "avg", f"{avg:.3f}")
            else:
                self.table.set(s, "min", "-")
                self.table.set(s, "max", "-")
                self.table.set(s, "avg", "-")

    # ------------------- Export -------------------
    def export_csv(self):
        if not self.all_data:
            messagebox.showinfo("Export", "No data to export yet.")
            return
        fp = filedialog.asksaveasfilename(defaultextension=".csv",
                                          filetypes=[("CSV files","*.csv"),("All files","*.*")],
                                          title="Export all received data")
        if not fp:
            return
        fieldnames = ["timestamp", "epoch_s", "V", "I", "C"]
        try:
            with open(fp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                for row in self.all_data:
                    w.writerow(row)
            messagebox.showinfo("Export", f"Saved {len(self.all_data)} rows to:\n{fp}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    # ------------------- Utils -------------------
    def _log(self, text):
        self.log_text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {text}\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")


def main():
    try:
        app = App()
        app.mainloop()
    finally:
        plt.close("all")


if __name__ == "__main__":
    main()
