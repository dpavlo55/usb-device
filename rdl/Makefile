PYTHON ?= python
# PYTHON is an executable path, including when passed from native Windows.
PYTHON_EXE := $(subst \,/,$(PYTHON))

RDL_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
RDL_SOURCE := $(RDL_DIR)usb_device.rdl
RTL_DIR := $(abspath $(RDL_DIR)../rtl)
HTML_DIR := $(abspath $(RDL_DIR)../html)
MODULE_NAME := usb_device_regs
CHEADER_FILE := $(abspath $(RDL_DIR)../sw/include/$(MODULE_NAME).h)
PYUVM_RAL_FILE := $(abspath $(RDL_DIR)../tb/uvm/usbtb/ral/usb_device_ral.py)

.DEFAULT_GOAL := all
.PHONY: all regs cheader html pyuvm help clean

all: regs cheader html pyuvm

regs:
	"$(PYTHON_EXE)" -c "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)" "$(RTL_DIR)"
	"$(PYTHON_EXE)" -m peakrdl regblock-vhdl "$(RDL_SOURCE)" \
		--default-reset arst_n \
		--cpuif axi4-lite-flat \
		--err-if-bad-addr \
		--err-if-bad-rw \
		--type-style hier \
		--module-name $(MODULE_NAME) \
		--hwif-report \
		--copy-utils-pkg \
		-o "$(RTL_DIR)"
	"$(PYTHON_EXE)" "$(RDL_DIR)fix_vhdl_array_indices.py" "$(RTL_DIR)/$(MODULE_NAME).vhd"

cheader:
	"$(PYTHON_EXE)" -c "from pathlib import Path; import sys; Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)" "$(CHEADER_FILE)"
	"$(PYTHON_EXE)" -m peakrdl c-header "$(RDL_SOURCE)" --bitfields ltoh --type-style hier -o "$(CHEADER_FILE)"

pyuvm:
	"$(PYTHON_EXE)" -m peakrdl pyuvm "$(RDL_DIR)regblock_udps.rdl" "$(RDL_SOURCE)" --type-style hier -o "$(PYUVM_RAL_FILE)"

html:
	"$(PYTHON_EXE)" -m peakrdl html "$(RDL_SOURCE)" -o "$(HTML_DIR)"

help:
	@"$(PYTHON_EXE)" -c "import sys; print('\n'.join(sys.argv[1:]))" \
		"all      Generate registers, C header, HTML documentation, and the pyuvm register model" \
		"regs     Generate the VHDL register block, utilities, and interface report" \
		"cheader  Generate the C register header with low-to-high bitfields" \
		"html     Generate HTML register documentation" \
		"pyuvm    Generate the pyuvm register model used by tb/uvm" \
		"clean    Remove generated register files, C header, and HTML documentation" \
		"help     Show available targets"

clean:
	"$(PYTHON_EXE)" -c "from pathlib import Path; import shutil, sys; [Path(p).unlink(missing_ok=True) for p in sys.argv[2:]]; html = Path(sys.argv[1]); shutil.rmtree(html) if html.exists() else None" \
		"$(HTML_DIR)" \
		"$(RTL_DIR)/$(MODULE_NAME).vhd" \
		"$(RTL_DIR)/$(MODULE_NAME)_pkg.vhd" \
		"$(RTL_DIR)/$(MODULE_NAME)_hwif.rpt" \
		"$(RTL_DIR)/reg_utils.vhd" \
		"$(CHEADER_FILE)" \
		"$(PYUVM_RAL_FILE)"
