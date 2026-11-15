.DEFAULT_GOAL := help

PUBLIC_DIR := public
PDF_DIR := $(PUBLIC_DIR)/pdf
IMG_DIR := $(PUBLIC_DIR)/img
PYTHON ?= python
MEMMAP ?= memmap
DOXYGEN ?= doxygen
ASCIIDOCTOR ?= asciidoctor
ASCIIDOCTOR_PDF ?= asciidoctor-pdf

# Doxygen pages are linked relative to the HTML data sheet; a PDF has no
# neighbour, so pass an absolute URL there (e.g. a GitHub Pages copy).
DOXYGEN_URL ?=

REVNUMBER := $(shell git describe --long --tags 2>/dev/null)
REV_ATTR := $(if $(REVNUMBER),-a revnumber=$(REVNUMBER),)
DOXYGEN_ATTR := $(if $(DOXYGEN_URL),-a doxygen-url=$(DOXYGEN_URL),)

.PHONY: all html pdf doxygen figures clean help

all: html doxygen pdf

html: | $(IMG_DIR)
	cp figures/*.svg $(IMG_DIR)/
	$(ASCIIDOCTOR) $(REV_ATTR) $(DOXYGEN_ATTR) datasheet/index.adoc --out-file $(PUBLIC_DIR)/index.html

pdf: | $(PDF_DIR)
	$(ASCIIDOCTOR_PDF) -r asciidoctor-mathematical -a mathematical-format=svg -a imagesoutdir=$(CURDIR)/$(PUBLIC_DIR)/stem $(REV_ATTR) $(DOXYGEN_ATTR) datasheet/main.adoc --out-file $(PDF_DIR)/usb_device_datasheet.pdf

# sw/Doxyfile is the single Doxygen configuration; only the output location changes, and
# Graphviz is taken from PATH instead of the Windows install path in the Doxyfile.
doxygen: | $(PUBLIC_DIR)
	cd ../sw && ( cat Doxyfile; echo; echo "OUTPUT_DIRECTORY = ../docs/$(PUBLIC_DIR)"; echo "HTML_OUTPUT = sw"; echo "DOT_PATH =" ) | "$(DOXYGEN)" -

# The SVGs are committed; regenerate them after changing figures/src/.
figures:
	"$(PYTHON)" figures/src/figures.py
	"$(MEMMAP)" render figures/src/address_space.yaml -o figures/address_space.svg --strict
	"$(MEMMAP)" render figures/src/double_buffer.yaml -o figures/double_buffer.svg --strict
	"$(PYTHON)" -c "import cairosvg; cairosvg.svg2png(url='figures/social_preview.svg', write_to='figures/social_preview.png', output_width=1280, output_height=640)"

$(PUBLIC_DIR) $(PDF_DIR) $(IMG_DIR):
	@mkdir -p $@

clean:
	@rm -rf $(PUBLIC_DIR)

help:
	@echo "Targets:"
	@echo " all     - HTML data sheet, Doxygen software reference and PDF data sheet"
	@echo " html    - HTML data sheet: $(PUBLIC_DIR)/index.html"
	@echo " doxygen - software reference next to it: $(PUBLIC_DIR)/sw/index.html"
	@echo " pdf     - PDF data sheet: $(PDF_DIR)/usb_device_datasheet.pdf"
	@echo " figures - regenerate the SVG figures (Python; memmap for the memory maps)"
	@echo " clean   - delete $(PUBLIC_DIR)"
	@echo "Variables:"
	@echo " DOXYGEN_URL - absolute base URL of the Doxygen pages (use it for pdf)"
	@echo " DOXYGEN, ASCIIDOCTOR, ASCIIDOCTOR_PDF - tool paths"
