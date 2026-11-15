# Project firmware; all generated files stay outside the NEORV32 submodule.
.DEFAULT_GOAL := image

PYTHON ?= python
# Forward slashes survive POSIX sh; quoting also supports spaces in the path.
PYTHON_EXE := $(subst \,/,$(PYTHON))
NEORV32_HOME := ../neorv32
MARCH := rv32im_zicsr_zifencei
MABI := ilp32
APP_SRC ?= main.c
APP_INC := -I . -I include
USER_FLAGS += -Wl,--defsym,__neorv32_rom_size=32k
USER_FLAGS += -Wl,--defsym,__neorv32_ram_size=16k
# Route UART0 TX writes to the simulator console; SIM=0 builds for hardware.
SIM ?= 1
ifeq ($(SIM),1)
USER_FLAGS += -DUART0_SIM_MODE
endif

# USB device library (sw/usb) and its NEORV32 port, linked into every program. The
# program's own directory comes first on the include path for its usb_config.h.
USB_LIB_SRC := $(wildcard usb/src/*.c)
USB_PORT_SRC := $(wildcard port/neorv32/*.c)
override APP_SRC += $(USB_LIB_SRC) $(USB_PORT_SRC)
# The register header, generated from rdl/usb_device.rdl.
REGS_H := include/usb_device_regs.h
APP_INC := -I $(dir $(firstword $(APP_SRC))) $(APP_INC) -I usb/include -I port/neorv32

# common.mk otherwise builds this host tool inside the submodule.
ifeq ($(OS),Windows_NT)
override IMAGE_GEN = $(BUILD_DIR)/image_gen.exe
else
override IMAGE_GEN = $(BUILD_DIR)/image_gen
endif

include $(NEORV32_HOME)/sw/common/common.mk

ifeq ($(OS),Windows_NT)
# The native Windows build tools do not provide chmod.
CHMOD = "$(PYTHON_EXE)" -c "import os,sys; os.chmod(sys.argv[2],0o755)"
endif

$(IMAGE_GEN): | $(BUILD_DIR)

# The library is compiled for speed; the rest keeps NEORV32's default effort.
USB_LIB_OBJ := $(patsubst %,$(BUILD_DIR)/%.o,$(notdir $(USB_LIB_SRC)))
$(USB_LIB_OBJ): EFFORT := -O2
$(USB_LIB_OBJ): $(REGS_H)

# Recompile application and library objects when compiler settings change.
$(OBJ): Makefile $(REGS_H)

$(REGS_H): ../rdl/usb_device.rdl ../rdl/Makefile
	$(MAKE) -C ../rdl cheader PYTHON="$(PYTHON_EXE)" CHEADER_FILE="$(abspath $@)"

ifdef SW_TEST
# common.mk searches by basename; explicitly name the test source so the
# application's main.c in this directory cannot be selected instead.
$(BUILD_DIR)/main.c.o: $(APP_SRC) $(REGS_H) | $(BUILD_DIR)
	$(Q)$(CC) -c $(NEO_CFLAGS) -MMD -MP -MF $(BUILD_DIR)/main.c.d -MT $@ -I $(NEORV32_INC_PATH) $(APP_INC) $< -o $@
else
$(BUILD_DIR)/main.c.o: $(REGS_H)
endif

.PHONY: run-sim
run-sim: image
	"$(PYTHON_EXE)" ../tools/cocotb_runner.py --firmware-only

# USB device library API docs (Doxyfile) into build/doxygen/html.
DOXYGEN ?= doxygen

.PHONY: docs
docs:
	"$(DOXYGEN)" Doxyfile
