# Dragonfly Neutron Detector Control (Python GUI)

A MegunoLink-compatible script to **monitor and control** the Dragonfly neutron detector over a serial connection.

- Rolling plot of **Voltage (V)**, **Current (µA)**, and **Counts/s** (default **60‑second** window)
- **HV setpoint** control with up/down arrows (0–1500 V) → sends `!SetHV <volts>\r\n`
- Live **min / max / avg** table over a **selectable time window** (seconds)
- **CSV export** of all received, time‑stamped data points
- Understands MegunoLink **TimePlot** messages and **plot control** commands

---

## Quick Start

```bash
# 1) Install dependencies
pip install -r requirements.txt

# 2) Run the GUI
python DragonflyNeutron_Rev01.py
```

1. Click **Refresh** to list serial ports, select your device, then **Connect**. (Default is **115200 baud**.)
2. Use the **High Voltage Setpoint** spinner (0–1500 V), then click **Send** to transmit `!SetHV <value>\r\n`.
3. Watch **V / I / C** update live in the plot and the stats table.
4. Click **Export CSV** to save all time‑stamped readings (`timestamp, epoch_s, V, I, C`).

> **Linux note:** If Tkinter is missing, install your distro’s Tk package (e.g., `python3-tk`). On some systems you may need to add your user to the `dialout` group to access serial ports, then log out/in.

---

## Features

- **Serial Manager**
  - Port dropdown, **Refresh**, **Connect**, **Disconnect**
  - 115200 baud (matching the Arduino sketch), configurable in code (`DEFAULT_BAUD`)

- **HV Control**
  - Integer **0–1500 V** setpoint via spinner
  - Transmits exactly: `!SetHV <value>\r\n`

- **Live Plot (Matplotlib)**
  - Left Y‑axis: **Voltage (V)**; Right Y‑axis: **Current (µA)** and **Counts/s**
  - Default rolling window: **60 s** (updated automatically if device sends an `XRANGE` command)
  - Legends, grid, and time‑formatted X‑axis

- **Stats Table**
  - **Min / Max / Avg** over a configurable window (spinner in seconds)

- **CSV Export**
  - Writes every collected sample to CSV with ISO timestamp and epoch seconds

---

## Protocol Compatibility (MegunoLink)

This app parses MegunoLink **TimePlot** messages and common plot controls:

### Data
- `{TIMEPLOT|DATA|V|T|<volts>}`  
- `{TIMEPLOT|DATA|I|T|<microamps>}`  
- `{TIMEPLOT|DATA|C|T|<counts_per_second>}`  
- Step/batch data: `{TIMEPLOT|DATA-STEP|V|T|<step_seconds>|y1|y2|...}` (also `DS`)

### Plot Controls
- X‑range (rolling window): `{TIMEPLOT|XRANGE|T|<hours>}` → e.g., `1.0/60.0` in your sketch = 60 s
- Y‑axis ranges: `{TIMEPLOT|YRANGE|<low>|<high>}`, `{TIMEPLOT|Y2RANGE|<low>|<high>}`
- Set labels/visibility: `{TIMEPLOT|SET|Title=...}`, `{TIMEPLOT|SET|X-Label=...}`, `{TIMEPLOT|SET|Y-Label=...}`, `{TIMEPLOT|SET|Y2-Label=...}`, `{TIMEPLOT|SET|Y-Visible=0|1}`, `{TIMEPLOT|SET|Y2-Visible=0|1}`
- Clear series/buffers: `{TIMEPLOT|CLEAR}` (all) or `{TIMEPLOT|CLEAR|V}` (single series)

> UI panel updates sent by the sketch (e.g., `Panel.SetNumber("gV", ...)`) are MegunoLink‑specific and safely ignored by this app.

---

## Configuration

Edit the top of `DragonflyNeutron_Rev01.py` to customize:

- `DEFAULT_BAUD = 115200` — Serial speed
- `SERIES_LEFT = ["V"]` — Series plotted on the left Y‑axis
- `SERIES_RIGHT = ["I", "C"]` — Series plotted on the right Y‑axis
- Initial Y‑axis limits (defaults mirror the sketch):  
  - Left: 0–2000 (V)  
  - Right: 0–250 (µA and cps)

> The rolling window will auto‑update if the device sends `{...|XRANGE|T|...}`; otherwise it defaults to 60 s.

---

## Requirements

- **Python 3.9+** on Windows/macOS/Linux
- **Tkinter** GUI toolkit (bundled with Python on Win/macOS; install `python3-tk` on many Linux distros)
- Python packages (from `requirements.txt`):
  - `pyserial`
  - `matplotlib`

Install with:

```bash
pip install -r requirements.txt
```

(Alternatively, install directly: `pip install pyserial matplotlib`.)

---

## Troubleshooting

- **No serial ports appear**: Check cable/driver; on Linux ensure your user is in **dialout** and re‑login.
- **Permission denied / port busy**: Close other apps using the port (serial monitors).
- **GUI fails to start**: Install Tkinter (`python3-tk`) or ensure a GUI backend is available for matplotlib.
- **No data plotted**: Confirm the device is sending MegunoLink **TIMEPLOT** messages for series **V**, **I**, **C**.

---

## Contributing

Pull requests are welcome. By contributing you agree your contributions are licensed under the project’s license (see below). Please keep the SPDX header in new/modified files.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL‑3.0)**.  
See the [LICENSE](./LICENSE) file for details.
