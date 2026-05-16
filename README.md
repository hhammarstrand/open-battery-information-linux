# OBI Linux

**OBI Linux** is a Linux port of [Open Battery Information][upstream]
(OBI) — tools and information about various batteries to aid in repair.

It is very common for manufacturers to lock the BMS when a fault is detected
to protect the device and the user. Very important feature! So when is it a
problem? Well, there is always a chance for false triggering of this
protection, or the fault could have been temporary or even repaired. In that
case it would be wasteful to throw out a perfectly good BMS just because its
software says it is faulty.

This is the problem we would like to solve!

![screenshot](docs/images/obi-1.png)

> **About this fork:** The upstream project ships Windows and macOS builds.
> This fork adds first-class support for **Pop!_OS, Ubuntu, and Debian** —
> a PyInstaller spec for Linux, a CI workflow that produces a tarball
> release, a `.desktop` file, a `udev` rule for the Arduino serial adapter,
> and an `install.sh` script. The application code itself is the same
> cross-platform Python/Tk codebase from upstream, with a small path-handling
> fix so it works when launched from a desktop entry.

[upstream]: https://github.com/mnh-jansson/open-battery-information

---

## Step 1: Set Up ArduinoOBI

1. Navigate to the `ArduinoOBI` folder in the project directory.
2. Follow the instructions in its `README.md` to flash the Arduino with
   the OBI firmware.

## Step 2: Install OBI Linux

You have three options.

### Option A — Precompiled binary for Pop!_OS / Ubuntu / Debian (recommended)

A standalone `x86_64` binary is built by CI and attached to every tagged
release as `obi-linux-x86_64.tar.gz`.

```bash
tar -xzf obi-linux-x86_64.tar.gz
cd obi-linux-pkg
sudo ./install.sh
```

`install.sh` will:

- install the binary as `/usr/local/bin/obi-linux`
- install the `.desktop` file and icon so OBI Linux appears in your
  application menu
- install a `udev` rule that makes the Arduino USB-serial adapter
  accessible without `sudo`
- add your user to the `plugdev` group

Log out and back in once, then launch **OBI Linux** from your application
menu (or run `obi-linux` from a terminal).

### Option B — Precompiled binary for Windows / macOS

Tagged releases also include a Windows `.exe` and a macOS `.dmg`. Download
the file for your platform from the Releases page and run it.

### Option C — Run from source (any platform)

```bash
git clone https://github.com/hhammarstrand/open-battery-information-linux
cd open-battery-information-linux/OpenBatteryInformation
pip install -r requirements.txt
python main.py
```

On Pop!_OS / Ubuntu / Debian, also install the system Tk package (it is not
provided by pip):

```bash
sudo apt install python3-tk
```

To get serial port access without running as root, either install the
`udev` rule (`sudo cp linux/90-arduino-obi.rules /etc/udev/rules.d/ && sudo
udevadm control --reload-rules && sudo udevadm trigger`) or add yourself to
the `dialout` group (`sudo usermod -aG dialout $USER`) and re-login.

## Building the Linux binary locally

```bash
sudo apt install python3-tk tk-dev binutils
cd OpenBatteryInformation
pip install pyinstaller -r requirements.txt
pyinstaller obi-linux.spec
# binary will be in dist/obi-linux
sudo ../linux/install.sh   # optional: install system-wide
```

---

## Supported batteries

- Makita LXT (5-cell packs, both standard and F0513 variants)

Module support is identical to upstream OBI; new modules and interfaces are
loaded dynamically from `OpenBatteryInformation/modules/` and
`OpenBatteryInformation/interfaces/`.

## Credits and acknowledgements

OBI Linux is built on top of [Open Battery Information][upstream] by Martin
Jansson. All battery-protocol reverse engineering and the original
application architecture are his work. If you find OBI Linux useful,
please consider supporting the upstream author:

- Contact the upstream author: openbatteryinformation@gmail.com
- [Buy the upstream author a coffee](https://www.buymeacoffee.com/mnhjansson)

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mnhjansson)

## License

MIT. The original upstream copyright is preserved in `LICENSE.md` together
with the copyright for the Linux-fork changes.
