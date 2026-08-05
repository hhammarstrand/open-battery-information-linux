# OBI Linux

Battery diagnostics for Makita LXT packs on **Pop!_OS, Ubuntu, Linux Mint and
Debian**, using an ArduinoOBI adapter. Desktop app plus a command line logger
for recording a pack over time.

- [Install](#install)
- [Serial port access](#serial-port-access)
- [Running the app](#running-the-app)
- [Logging over time](#logging-over-time)
- [No hardware? Use the simulator](#no-hardware-use-the-simulator)
- [Troubleshooting](#troubleshooting)
- [Environment variables](#environment-variables)
- [Building from source](#building-from-source)

---

## Install

### From the release tarball (recommended)

```bash
tar -xzf obi-linux-x86_64.tar.gz
cd obi-linux-x86_64
sudo ./install.sh
```

This installs:

| What | Where |
| --- | --- |
| Desktop app | `/usr/local/bin/obi-linux` |
| Command line logger | `/usr/local/bin/obi-log` |
| Menu entry and icon | `/usr/local/share/applications`, `.../icons` |
| udev rule for the adapter | `/etc/udev/rules.d/90-arduino-obi.rules` |

It also adds you to the `plugdev` group. **Log out and back in once**, then
launch *OBI Linux* from your application menu.

Check that the installation is sane at any time:

```bash
obi-linux --selftest
```

Remove everything again with `sudo ./install.sh --uninstall` (your logs are
kept).

### From source

```bash
sudo apt install python3-tk            # tkinter is a separate package on Linux
git clone https://github.com/hhammarstrand/open-battery-information-linux
cd open-battery-information-linux/OpenBatteryInformation
pip install -r requirements.txt
python3 main.py
```

---

## Serial port access

This is the part that trips people up on Linux. The adapter shows up as
`/dev/ttyUSB0` (CH340, FTDI) or `/dev/ttyACM0` (Arduino Uno, ESP32-C3), and by
default only `root` and one system group may use it.

`install.sh` handles it. To do it by hand:

```bash
sudo cp linux/90-arduino-obi.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"      # log out and back in
```

The rule does two things: it grants access to the `plugdev` group and to the
locally logged-in user, and it tells **ModemManager** to ignore the adapter.
That second part matters: on Ubuntu, Pop!_OS and Mint, ModemManager probes
every new serial device with AT commands for several seconds, which corrupts
the first commands OBI sends and looks exactly like broken hardware.

If you would rather not install the rule, adding yourself to the group that
owns the device also works:

```bash
sudo usermod -aG dialout "$USER"      # log out and back in
```

OBI never needs `sudo` once permissions are right - and you should not run a
GUI as root anyway.

---

## Running the app

1. Plug in the adapter, put the battery on the contacts.
2. Start *OBI Linux*.
3. Sidebar → **Select Interface: Arduino OBI**, pick the port, press
   **Connect**. The firmware version appears when the adapter answers.
4. Sidebar → **Module Selection: Makita LXT**.
5. **Read battery model**, then **Read battery data**.

The port dropdown hides built-in UARTs (`/dev/ttyS0` and friends) because they
are never the adapter; tick *Show non-USB ports* if you need them. When
exactly one USB adapter is present it is preselected.

---

## Logging over time

Both the app and the CLI write the same format, so a log started in the GUI
can be continued from the command line.

### In the app

Under the readings table there is a **Data logging (over time)** panel:

- **Interval** - seconds between samples.
- **Include state / charge count** - adds lock state, status code and charge
  count. Costs one extra round trip per sample.
- **Format** - `csv` or `jsonl`.
- **File** - defaults to a timestamped file in
  `~/.local/share/obi-linux/logs/`.

Press **Start logging**. The panel keeps counting samples and errors while you
keep using the app; logging happens on a background thread and each row is
flushed to disk immediately, so an unplugged adapter or a crash never costs
more than the current sample. Logging stops by itself after 10 consecutive
failed samples.

### From the command line

```bash
obi-log --list-ports                     # what is connected?
obi-log --once                           # one reading, printed
obi-log --interval 60 --duration 8h      # log for eight hours
obi-log --interval 30 --out ~/pack1.csv --include-status --append
```

Useful flags: `--port` (autodetected when unambiguous), `--samples N`,
`--format jsonl`, `--boot-delay 0` (native-USB boards do not reset on connect),
`--quiet`, `--verbose` (prints every protocol frame). `--duration` accepts
`90s`, `15m`, `8h`, `2d`.

### Unattended, as a service

```bash
mkdir -p ~/.config/systemd/user
cp linux/obi-log.service ~/.config/systemd/user/
# edit ExecStart: set --port to the /dev/serial/by-id/... path and the interval
systemctl --user daemon-reload
systemctl --user enable --now obi-log
journalctl --user -u obi-log -f
```

Use the `/dev/serial/by-id/...` path that `obi-log --list-ports` prints: it
survives replugging, unlike `/dev/ttyUSB0`.

### What ends up in the file

| Column | Meaning |
| --- | --- |
| `timestamp` | Local time with UTC offset, e.g. `2026-08-05T13:30:00+02:00` |
| `elapsed_s` | Seconds since logging started |
| `sample` | Sample number, starting at 1 |
| `model`, `rom_id` | Identity, read once when logging starts |
| `pack_v` | Pack voltage (summed cells on F0513 packs) |
| `cell1_v` … `cell5_v` | Individual cell voltages |
| `cell_delta_v` | Highest minus lowest cell, in millivolt resolution |
| `temp1_c`, `temp2_c` | Temperature sensors (`temp2_c` is empty on F0513) |
| `state`, `status_code`, `charge_count` | Only with `--include-status` |
| `error` | Empty on success, otherwise why this sample failed |

Failed samples are written as rows with an `error` value rather than being
skipped, so a gap in the data is visible instead of silently missing.

`cell_delta_v` is the number to watch: a healthy pack stays within a few
millivolts, and a delta that grows as the pack drains points at the weak cell.

---

## No hardware? Use the simulator

`tools/fake_adapter.py` emulates an adapter with a battery on it, over a
pseudo terminal - useful for trying the app, developing modules, or checking
the logger:

```bash
cd OpenBatteryInformation
python3 tools/fake_adapter.py --drain 5      # prints e.g. /dev/pts/5
```

Then point either program at it:

```bash
obi-log --port /dev/pts/5 --boot-delay 0 --interval 2
OBI_EXTRA_PORTS=/dev/pts/5 python3 main.py   # the pty shows up in the dropdown
```

Options: `--f0513` (the diagnostics-only command set), `--locked`,
`--status 0x1B`, `--flaky 0.3` (drop 30 % of answers), `--drain` mV/s.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'tkinter'` | tkinter is packaged separately on Linux | `sudo apt install python3-tk` (not needed for the release binary) |
| No ports in the dropdown | Adapter not detected | `dmesg --follow`, replug; try another cable - charge-only cables are common |
| "You do not have read/write access" | udev rule missing or group not applied | Install the rule, or `sudo usermod -aG dialout $USER`, then **log out and back in** |
| "The port is already open by ..." | Serial monitor or a second OBI instance | Close the other program |
| First read after plugging in fails, later ones work | ModemManager probing the adapter | Install the udev rule, or wait ~10 s |
| "No response received from the adapter" | Adapter not flashed, wrong board, or wiring | Flash `ArduinoOBI`, check the pull-ups and the ENABLE/ONEWIRE pins |
| "Invalid response: all bytes are 0xFF" | Adapter answers, battery does not | Reseat the pack; check the ONEWIRE contact |
| Reads time out on a BL18xx F0513 pack | That command path is slow (~900 ms) | Raise the timeout: `OBI_TIMEOUT=3 obi-linux` |
| Adapter disappears mid-log | USB re-enumeration | The link reconnects automatically; use a `/dev/serial/by-id/...` path |
| Everything is tiny on a HiDPI screen | Tk ignores the desktop scale factor | `OBI_SCALING=1.5 obi-linux` |

Nothing here needs `sudo`. If a suggestion tells you to run OBI as root,
ignore it and fix the permissions instead.

---

## Environment variables

| Variable | Effect |
| --- | --- |
| `OBI_SCALING` | Tk scale factor for HiDPI displays, e.g. `1.5` |
| `OBI_EXTRA_PORTS` | Extra serial devices for the picker, colon separated (pty, socat, RFC2217) |
| `OBI_TIMEOUT` | Serial read timeout in seconds (default 2.0) |
| `OBI_BOOT_DELAY` | Wait after opening the port for the board to boot (default 2.0; use 0 for native-USB boards) |
| `XDG_DATA_HOME` | Where logs go: `$XDG_DATA_HOME/obi-linux/logs` |

---

## Building from source

```bash
sudo apt install python3-tk tk-dev binutils xvfb
cd OpenBatteryInformation
pip install pyinstaller -r requirements.txt
python3 -m PyInstaller --noconfirm obi-linux.spec   # -> dist/obi-linux
python3 -m PyInstaller --noconfirm obi-log.spec     # -> dist/obi-log
sudo ../linux/install.sh
```

Or from the repository root: `make deps`, `make test`, `make binary`,
`make install`.

Run the tests with `make test`. They need neither an adapter nor a battery:
protocol decoding, the logger and the port helpers are covered with fakes, and
the GUI and end-to-end tests run against the pty simulator (skipped
automatically when there is no display - use `make test-headless` for Xvfb).

Binaries are built on Ubuntu 22.04 in CI so that they also run on Ubuntu
24.04, Mint 21/22 and current Pop!_OS. Building on a newer distro produces a
binary that will *not* run on older ones, because of glibc.
