# OBI Linux - developer conveniences.
#
#   make deps      install the system + Python dependencies (apt based distros)
#   make run       run the app from source
#   make test      run the test suite (no hardware needed)
#   make simulator start a fake adapter on a pty, to try things without hardware
#   make binary    build dist/obi-linux and dist/obi-log with PyInstaller
#   make install   install the built binaries system-wide (asks for sudo)
#   make clean     remove build artefacts

PYTHON  ?= python3
APP_DIR := OpenBatteryInformation

.PHONY: help deps run test test-headless simulator binary install uninstall clean

help:
	@sed -n '3,10p' Makefile | sed 's/^# \{0,1\}//'

deps:
	sudo apt-get update
	sudo apt-get install -y python3-tk xvfb binutils
	$(PYTHON) -m pip install -r $(APP_DIR)/requirements.txt

run:
	cd $(APP_DIR) && $(PYTHON) main.py

test:
	cd $(APP_DIR) && $(PYTHON) -m unittest discover -s tests -t . -v

# Same suite with a virtual display, for machines without a desktop session.
test-headless:
	cd $(APP_DIR) && xvfb-run -a $(PYTHON) -m unittest discover -s tests -t . -v

simulator:
	cd $(APP_DIR) && $(PYTHON) tools/fake_adapter.py

binary:
	cd $(APP_DIR) && $(PYTHON) -m PyInstaller --noconfirm obi-linux.spec
	cd $(APP_DIR) && $(PYTHON) -m PyInstaller --noconfirm obi-log.spec
	@echo "Built $(APP_DIR)/dist/obi-linux and $(APP_DIR)/dist/obi-log"

install: binary
	sudo linux/install.sh

uninstall:
	sudo linux/install.sh --uninstall

clean:
	rm -rf $(APP_DIR)/build $(APP_DIR)/dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
