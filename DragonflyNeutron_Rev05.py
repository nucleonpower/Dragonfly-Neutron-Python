#!/usr/bin/env python3
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
#
#
# Dragonfly Neutron – Nucleon Power
# - HV setpoint Spinbox with arrow buttons; 15 V step; 0..1500; sends on every click
# - Parses {TIMEPLOT|DATA|<Series>|T|<Value>} for C, I, V and plots live
#
# Device speaks lines like:
#   {TIMEPLOT|DATA|C|T|123}
#   {TIMEPLOT|DATA|I|T|7}
#   {TIMEPLOT|DATA|V|T|1200}
# High Voltage Bias Command "!SetHV <int>\r\n"


import time
import threading
import queue
import re
from datetime import datetime
import csv
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import serial
from serial.tools import list_ports

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

APP_TITLE = "Dragonfly Neutron - Rev.02 - Nucleon Power, Inc. (c) 2025"
BAUD = 115200
HV_MIN, HV_MAX, HV_STEP = 0, 1500, 15
WINDOW_SECONDS = 60                  # show the last 60 s (scrolling)
READ_TIMEOUT = 0.1                   # serial read timeout
CURRENT_YMIN, CURRENT_YMAX = 0, 250  # fixed current (µA) axis

# {TIMEPLOT|DATA|C|T|0} etc.
TIMEPLOT_RE = re.compile(r"\{TIMEPLOT\|DATA\|([CIV])\|T\|(-?\d+(?:\.\d+)?)\}")

def list_serial_ports():
    return [p.device for p in list_ports.comports()]

class SerialReader(threading.Thread):
    def __init__(self, ser: serial.Serial, out_queue: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.ser = ser
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.buffer = bytearray()

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    data = self.ser.read(1024)
                    if not data:
                        continue
                    self.buffer.extend(data)
                    while b"\n" in self.buffer:
                        line, _, rest = self.buffer.partition(b"\n")
                        self.buffer = rest
                        txt = line.decode("ascii", errors="ignore").strip()
                        if txt:
                            self._parse_line(txt)
                except serial.SerialException:
                    break
        finally:
            pass

    def _parse_line(self, line: str):
        m = TIMEPLOT_RE.fullmatch(line)
        if m:
            series, value = m.group(1), float(m.group(2))
            ts = time.time()  # timestamp as received
            self.out_queue.put((series, value, ts))

class DragonflyGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x700")
        self.minsize(1000, 600)

        # Serial + threading
        self.ser = None
        self.reader_thread = None
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()

        # Data (retain everything for CSV, wide format)
        self.t0 = time.time()
        self.C_t, self.C_y = [], []
        self.I_t, self.I_y = [], []
        self.V_t, self.V_y = [], []

        # last-known values for wide CSV snapshot
        self.last_counts = None
        self.last_voltage = None
        self.last_current = None
        # rows: (iso_ts, t_rel, Counts, Voltage, Current)
        self.data_wide_log = []

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------- UI ----------
    def _build_ui(self):
        # Top row with groups + status on the right
        row1 = ttk.Frame(self, padding=8)
        row1.pack(side=tk.TOP, fill=tk.X)

        # Communication group
        comm = ttk.LabelFrame(row1, text="Communication", padding=8)
        comm.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(comm, text="Port:").pack(side=tk.LEFT, padx=(0, 6))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(comm, textvariable=self.port_var, width=28, state="readonly")
        self._refresh_ports()
        self.port_combo.pack(side=tk.LEFT)
        ttk.Button(comm, text="Refresh", command=self._refresh_ports).pack(side=tk.LEFT, padx=(6, 8))
        self.connect_btn = ttk.Button(comm, text="Connect", command=self._connect)
        self.connect_btn.pack(side=tk.LEFT)
        self.disconnect_btn = ttk.Button(comm, text="Disconnect", command=self._disconnect, state=tk.DISABLED)
        self.disconnect_btn.pack(side=tk.LEFT, padx=(6, 0))

        # Detector Bias group
        hv = ttk.LabelFrame(row1, text="Detector Bias (V)", padding=8)
        hv.pack(side=tk.LEFT, padx=10)
        self.hv_var = tk.IntVar(value=0)
        self.hv_spin = ttk.Spinbox(hv, from_=HV_MIN, to=HV_MAX, increment=HV_STEP,
                                   textvariable=self.hv_var, width=10, command=self._send_hv_from_spin)
        self.hv_spin.pack(side=tk.LEFT)
        ttk.Button(hv, text="Send", command=self._send_hv_from_spin).pack(side=tk.LEFT, padx=(8, 0))

        # Data group (CSV)
        data = ttk.LabelFrame(row1, text="Data", padding=8)
        data.pack(side=tk.LEFT, padx=10)
        ttk.Button(data, text="Export CSV", command=self._export_csv).pack(side=tk.LEFT)

        # Right-aligned status (last message only)
        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(row1, textvariable=self.status_var).pack(side=tk.RIGHT)

        # Plot area
        plot_frame = ttk.Frame(self, padding=8)
        plot_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        fig = Figure(figsize=(6, 4), dpi=100)
        # Make room on the right for the extra axis
        fig.subplots_adjust(right=0.82)

        # Axes: Voltage (left, autoscale), Counts (right, autoscale), Current (right2, fixed)
        self.ax_v = fig.add_subplot(111)
        self.ax_v.set_title("Dragonfly Neutron")
        self.ax_v.set_xlabel("Time (s)")
        self.ax_v.set_ylabel("Voltage (V)")
        self.ax_v.set_ylim(0, 1500)

        self.ax_c = self.ax_v.twinx()
        self.ax_c.set_ylabel("Counts (CPS)")

        self.ax_i = self.ax_v.twinx()
        self.ax_i.set_ylabel("Current (µA)")
        # Visible, offset right
        self.ax_i.spines["right"].set_position(("outward", 60))
        self.ax_i.spines["right"].set_visible(True)
        self.ax_i.patch.set_visible(False)
        self.ax_c.spines["right"].set_visible(True)

        # Lines with requested colors
        self.line_v, = self.ax_v.plot([], [], color="green", label="V (Volts)")
        self.line_c, = self.ax_c.plot([], [], color="red",   label="C (CPS)")
        self.line_i, = self.ax_i.plot([], [], color="blue",  label="I (µA)")

        # Color-code the y-axes
        self.ax_v.yaxis.label.set_color("green")
        self.ax_c.yaxis.label.set_color("red")
        self.ax_i.yaxis.label.set_color("blue")
        self.ax_v.tick_params(axis="y", colors="green")
        self.ax_c.tick_params(axis="y", colors="red")
        self.ax_i.tick_params(axis="y", colors="blue")

        # Fixed current axis range
        self.ax_i.set_ylim(CURRENT_YMIN, CURRENT_YMAX)

        # Legend
        handles = [self.line_v, self.line_c, self.line_i]
        labels = [h.get_label() for h in handles]
        self.ax_v.legend(handles, labels, loc="upper left")

        self.canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Light theme if present
        try:
            style = ttk.Style(self)
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_combo["values"] = ports
        if ports and (self.port_var.get() not in ports):
            self.port_var.set(ports[0])
        if not ports:
            self.port_var.set("")

    # ---------- Serial ----------
    def _connect(self):
        if self.ser and self.ser.is_open:
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("No port", "Select a serial port first.")
            return
        try:
            self.ser = serial.Serial(port, BAUD, timeout=READ_TIMEOUT)
        except Exception as e:
            messagebox.showerror("Connection error", f"Could not open {port}:\n{e}")
            self.ser = None
            return

        self._set_status(f"Connected @ {port} ({BAUD} bps)")
        self.connect_btn.configure(state=tk.DISABLED)
        self.disconnect_btn.configure(state=tk.NORMAL)

        self.stop_event.clear()
        self.reader_thread = SerialReader(self.ser, self.data_queue, self.stop_event)
        self.reader_thread.start()

        self._reset_series()

    def _disconnect(self):
        self.stop_event.set()
        if self.reader_thread:
            self.reader_thread.join(timeout=1.0)
        self.reader_thread = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self._set_status("Disconnected")
        self.connect_btn.configure(state=tk.NORMAL)
        self.disconnect_btn.configure(state=tk.DISABLED)

    # ---------- HV send ----------
    def _send_hv_from_spin(self):
        val = int(self.hv_var.get())
        val = max(HV_MIN, min(HV_MAX, val))
        self.hv_var.set(val)
        self._send_hv(val)

    def _send_hv(self, val: int):
        if not (self.ser and self.ser.is_open):
            self._set_status("Not connected; command not sent.")
            return
        cmd = f"!SetHV {val}\r\n"
        try:
            self.ser.write(cmd.encode("ascii"))
            self._set_status(f"Sent: {cmd.strip()}")
        except Exception as e:
            self._set_status(f"Send error: {e}")

    # ---------- Data / Plot ----------
    def _reset_series(self):
        self.t0 = time.time()
        self.C_t.clear(); self.C_y.clear()
        self.I_t.clear(); self.I_y.clear()
        self.V_t.clear(); self.V_y.clear()
        self.last_counts = None
        self.last_voltage = None
        self.last_current = None
        self.data_wide_log.clear()
        self._redraw()

    def _poll_queue(self):
        updated = False
        try:
            while True:
                series, value, ts = self.data_queue.get_nowait()
                t_rel = ts - self.t0
                iso_ts = datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")

                # Update plots + last-known values
                if series == "C":
                    self.C_t.append(t_rel); self.C_y.append(value)
                    self.last_counts = value
                elif series == "I":
                    self.I_t.append(t_rel); self.I_y.append(value)
                    self.last_current = value
                elif series == "V":
                    self.V_t.append(t_rel); self.V_y.append(value)
                    self.last_voltage = value

                # Wide CSV snapshot (carry-forward latest values)
                self.data_wide_log.append((
                    iso_ts,
                    t_rel,
                    self.last_counts if self.last_counts is not None else "",
                    self.last_voltage if self.last_voltage is not None else "",
                    self.last_current if self.last_current is not None else "",
                ))

                updated = True
        except queue.Empty:
            pass

        if updated:
            self._redraw()

        self.after(100, self._poll_queue)

    def _slice_window(self, t_list, y_list, xmin):
        if not t_list:
            return [], []
        i0 = 0
        for idx in range(len(t_list)-1, -1, -1):
            if t_list[idx] < xmin:
                i0 = idx + 1
                break
        return t_list[i0:], y_list[i0:]

    def _redraw(self):
        # X limits: last 60 s (scrolling)
        xmax_candidates = []
        if self.C_t: xmax_candidates.append(self.C_t[-1])
        if self.I_t: xmax_candidates.append(self.I_t[-1])
        if self.V_t: xmax_candidates.append(self.V_t[-1])
        if xmax_candidates:
            xmax = max(xmax_candidates)
            xmin = max(0.0, xmax - WINDOW_SECONDS)
        else:
            xmin, xmax = 0.0, WINDOW_SECONDS

        # Windowed data for display (full data retained)
        Vx, Vy = self._slice_window(self.V_t, self.V_y, xmin)
        Cx, Cy = self._slice_window(self.C_t, self.C_y, xmin)
        Ix, Iy = self._slice_window(self.I_t, self.I_y, xmin)

        self.line_v.set_data(Vx, Vy)
        self.line_c.set_data(Cx, Cy)
        self.line_i.set_data(Ix, Iy)

        # X range (scroll)
        self.ax_v.set_xlim(xmin, max(xmin + 10, xmax))

        # Voltage fixed 0–1500 (no autoscale)
        self.ax_v.set_ylim(0, 1500)

        # Counts autoscale
        self.ax_c.relim(); self.ax_c.autoscale_view(scalex=False, scaley=True)

        # Current axis fixed
        self.ax_i.set_ylim(CURRENT_YMIN, CURRENT_YMAX)

        self.canvas.draw_idle()

    # ---------- CSV export ----------
    def _export_csv(self):
        if not self.data_wide_log:
            messagebox.showinfo("Export CSV", "No data to export yet.")
            return
        fname = filedialog.asksaveasfilename(
            title="Export data to CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not fname:
            return
        try:
            with open(fname, "w", newline="") as f:
                w = csv.writer(f)
                # exact header order requested
                w.writerow(["timestamp_iso", "t_rel_seconds", "Counts", "Voltage", "Current"])
                w.writerows(self.data_wide_log)
            self._set_status(f"Saved CSV: {fname}")
        except Exception as e:
            messagebox.showerror("Export error", f"Couldn't write file:\n{e}")

    # ---------- status (last message only) ----------
    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def on_close(self):
        try:
            self._disconnect()
        finally:
            self.destroy()

def main():
    app = DragonflyGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()

if __name__ == "__main__":
    main()
