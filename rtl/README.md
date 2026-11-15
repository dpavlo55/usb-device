# RTL

VHDL-2008, vendor-neutral. `usb_top.vhd` is the top level; see
the data sheet in [`docs/datasheet/`](../docs/datasheet/) for how the blocks fit together.
The compilation order is the list in `tools/cocotb_runner.py`.

| File | Role |
|---|---|
| `usb_top.vhd` | top level: AXI4-Lite, USB pins, VBUS, pull-up, irq |
| `usb_pkg.vhd`, `stream_pkg.vhd` | shared types and constants |
| `usb_phy.vhd`, `usb_phy_rx.vhd`, `usb_phy_tx.vhd` | line receiver (4× oversampling, NRZI, unstuffing, reset detection) and transmitter |
| `usb_packet_deserializer.vhd`, `usb_packet_parser.vhd` | bits to bytes; PID and CRC checks, packet records |
| `usb_packet_generator.vhd`, `usb_packet_serializer.vhd` | handshake and DATA packets; bytes to bits |
| `crc.vhd` | CRC5 / CRC16 |
| `usb_protocol.vhd` | transactions: routing, handshakes, data toggles, double buffering |
| `usb_rx_filter.vhd`, `usb_setup_stage.vhd` | OUT/SETUP payload routing, size limit, SETUP write after acceptance |
| `usb_endpoints.vhd`, `usb_endpoints_controller.vhd` | endpoint entries: arm/complete, events, split IN transfers |
| `usb_ram.vhd`, `dual_port_ram.vhd`, `usb_ram_sw_adapter.vhd`, `usb_ram_usb_adapter.vhd` | shared buffer RAM (2^`RAM_ADDR_WIDTH` bytes) and its two ports |
| `usb_bus_state.vhd` | suspend, resume, remote wakeup, SOF |
| `usb_cdc.vhd`, `cdc_*.vhd` | clock-domain crossing between `clk_sys` and `clk_usb` |
| `usb_device_regs.vhd`, `usb_device_regs_pkg.vhd`, `reg_utils.vhd` | register block **generated** from `rdl/usb_device.rdl` (`make -C rdl regs`); do not edit |
| `input_buffer.vhd`, `output_buffer.vhd`, `single_buffer.vhd`, `elastic_buffer.vhd`, `elastic_fifo.vhd`, `edge_detector.vhd` | AXI4-Stream buffering helpers |
