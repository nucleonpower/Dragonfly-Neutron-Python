# Dragonfly Neutron – Python GUI

A lightweight, cross‑platform Python GUI that replaces the MegunoLink panel for your Dragonfly Neutron detector interface.  
It communicates over serial, sends **Detector Bias** setpoints, parses MegunoLink‑style **TimePlot** messages, and plots **Counts (CPS)**, **Voltage (V)**, and **Current (µA)** in real time. It also exports data to CSV.

---

## Features

- **Serial control**: Port dropdown, Refresh, Connect/Disconnect (115200 bps by default)
- **Detector Bias control**: Spinbox with **15 V** steps (0–1500)  
  → every click immediately sends `!SetHV <value>\r\n`
- **Live plotting**:
  - **Voltage** (green) on left axis *(can be fixed at 0–1500; see Configuration)*
  - **Counts** (red) on right axis (autoscale)
  - **Current** (blue) on second right axis (fixed range, default 0–250 µA)
  - A **60‑second scrolling window** is displayed; **all data is retained** in memory
- **CSV export (wide format)** with header:  
  `timestamp_iso, t_rel_seconds, Counts, Voltage, Current`

---

## System Requirements

- **OS**: Windows 10/11, macOS 12+ (Intel/Apple Silicon), or Linux (x86_64/ARM)
- **Python**: 3.9 or newer
- **Python packages**:
  - `pyserial`
  - `matplotlib`
  - (GUI uses Tkinter, which ships with most Python installers)
- **Drivers (if needed)**:
  - USB‑UART drivers (e.g. FTDI). Install the vendor driver if the port does not appear.

### Install the Python deps

```bash
pip install pyserial matplotlib
```

> **Linux note (Tkinter)**: if Tk isn’t present, install your distro’s package (e.g., `sudo apt-get install python3-tk`).  
> **macOS note**: the official Python from python.org includes Tk. If you use Homebrew Python and see Tk errors, install a Tk package or switch to the python.org build.

---

## Getting Started

1. **Attach** He-3 Detector tube to interface
2. **Connect** the interface via USB.
3. **Run** the app:

   ```bash
   python DragonflyNeutron_Rev02.py
   ```

4. In **Communication**: **choose the Port → Connect**.  
   The status on the top row shows `Connected @ <port> (115200 bps)`.

5. In **Detector Bias (V)**:
   - Use the arrows to step by **15 V** (0–1500).  
   - Every click immediately sends: `!SetHV <integer>\r\n` (e.g., `!SetHV 450\r\n`).

6. **Plot** colors and axes:
   - Green (Voltage) on **left y‑axis**  
   - Red (Counts) on **right y‑axis** *(autoscale)*  
   - Blue (Current) on **second right y‑axis** *(fixed, default 0–250 µA)*
   - The view **scrolls over the last 60 seconds**, while the app **retains all data** until you close it.

7. In **Data**: click **Export CSV**.  
   A file dialog lets you pick the destination. The CSV has:

   ```text
   timestamp_iso,t_rel_seconds,Counts,Voltage,Current
   2025-03-01T12:00:00.100,0.100,12,742,18
   2025-03-01T12:00:00.200,0.200,12,744,19
   ...
   ```

   - `timestamp_iso`: wall‑clock timestamp when the message was received (millisecond precision)  
   - `t_rel_seconds`: seconds since you connected  
   - `Counts`, `Voltage`, `Current`: the **latest known** values at that moment (carry‑forward semantics)

---

## User Interface Overview

- **Communication** – Port dropdown (auto‑populated), **Refresh**, **Connect**, **Disconnect**.  
- **Detector Bias (V)** – Spinbox 0–1500 V with **15 V** increments + **Send** button. Clicking the arrows (or **Send**) **immediately transmits** the setpoint.  
- **Data** – **Export CSV** button.  
- **Status (top right)** – Shows only the **most recent message** (e.g., connected/disconnected, send errors, CSV saved).  
- **Plot** – Left: **Voltage** (green), Right: **Counts** (red), Second‑right: **Current** (blue, fixed). X‑axis shows **last 60 s** (scrolling).

---

## Serial Communication Details

### Port settings

- **Baud**: 115200  
- **Framing**: 8‑N‑1  
- **Line endings**: device can send `\r\n` or `\n` (reader splits on `\n`).

### Outgoing command (host → device)

- **Set Detector Bias**  
  ```text
  !SetHV <integer>\r\n
  ```
  - `<integer>` range: **0–1500** (volts)
  - Examples:
    - `!SetHV 0\r\n` (turns HVPS off per your firmware)
    - `!SetHV 450\r\n`
    - `!SetHV 1500\r\n`

The GUI sends this exact ASCII string whenever the spinbox arrows are clicked (or you press **Send**).

### Incoming messages (device → host)

The app listens for MegunoLink **TimePlot** data packets and ignores everything else.

**Grammar**

```text
{TIMEPLOT|DATA|<Series>|T|<Value>}
```

- `<Series>`: one of
  - `C` → **Counts** (CPS)
  - `I` → **Current** (µA)
  - `V` → **Voltage** (V)
- `<Value>`: integer or decimal number (ASCII)

**Examples (one per line)**

```text
{TIMEPLOT|DATA|C|T|0}
{TIMEPLOT|DATA|I|T|30}
{TIMEPLOT|DATA|V|T|120}
```

**Timing**

Your firmware typically updates every ~100 ms. The GUI timestamps each message **as received**, updates the plot, and appends a row to the **wide CSV log** with the latest known **Counts/Voltage/Current** at that instant.

---

## CSV Format & Semantics

- **Header (exact order)**  
  ```text
  timestamp_iso,t_rel_seconds,Counts,Voltage,Current
  ```

- **timestamp_iso** – wall‑clock ISO 8601 with milliseconds  
- **t_rel_seconds** – seconds since you connected  
- **Counts / Voltage / Current** – “carry‑forward” values: if a `C` update arrives, that row shows the *latest* counts together with the *latest* voltage and current seen so far. Early rows may contain blanks until each series has appeared at least once.

> Prefer a “long” format (one row per series per time) or strict triplets (only when C/I/V arrive within the same tick)? That’s easy to switch—ask for a variant.

---

## Configuration & Customization

Open `DragonflyNeutron_Rev02.py` and adjust these constants near the top:

```python
BAUD = 115200
HV_MIN, HV_MAX, HV_STEP = 0, 1500, 15
WINDOW_SECONDS = 60                  # plot’s scrolling window
CURRENT_YMIN, CURRENT_YMAX = 0, 250  # fixed current axis (µA)
```

### Fixing axis ranges

- **Voltage** (left axis) fixed at **0–1500**:

  ```python
  # after creating self.ax_v
  self.ax_v.set_ylim(0, 1500)

  # in _redraw(), don't autoscale the left axis; keep set_ylim(0, 1500)
  # (Counts autoscale; Current stays fixed to CURRENT_YMIN..CURRENT_YMAX)
  ```

- **Current** (second right axis) is already fixed to `CURRENT_YMIN..CURRENT_YMAX`.  
- **Counts** (right axis) autoscale by default.

### Changing the visible window

Set `WINDOW_SECONDS` to your preferred horizon (e.g., 120 for 2 minutes).

---

## Troubleshooting

- **Port doesn’t show up**  
  Click **Refresh**. Install the correct USB‑UART driver (FTDI).  
  On Linux, ensure your user is in the `dialout` group (or equivalent), then re‑login.

- **Connect succeeds but no data**  
  Verify the detector interface is running and sending lines like `{TIMEPLOT|DATA|C|T|...}` at **115200**.  
  Test with Arduino Serial Monitor (close it before using this app). Check cables/power.

- **Graph not moving / axis looks wrong**  
  Ensure the message format exactly matches the grammar above.  
  If you fixed the voltage axis, confirm `_redraw()` is **not** calling `autoscale_view` for the left axis.

- **CSV is empty**  
  Export only writes data that arrived **since you connected**. Keep the app running while collecting.

---

## Safety Notes

You are controlling **high voltage**. Ensure detector is attached before adjusting bias and never disconnect with bias applied or reconnect detector if recently biased. 
The app clamps setpoints to **0–1500 V** and sends integers only, but it cannot detect hardware faults.

---

## Folder Layout

```text
project/
├─ dragonfly_gui.py      # the GUI application
└─ README.md             # this file
```

---

## Contributing

Pull requests are welcome. By contributing you agree your contributions are licensed under the project’s license (see below). Please keep the SPDX header in new/modified files.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL‑3.0)**.  
See the [LICENSE](./LICENSE) file for details.

---

## Credits

Built around an Arduino firmware that uses MegunoLink `CommandHandler`, `TimePlot`, and related utilities, adapted to a minimal Python GUI workflow.
