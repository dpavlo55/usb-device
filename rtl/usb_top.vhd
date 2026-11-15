library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;
use work.usb_device_regs_pkg.all;

entity usb_top is
    port (
        clk_sys      : in    std_ulogic;
        rst_sys_n    : in    std_ulogic;
        clk_usb      : in    std_ulogic;
        rst_usb_n    : in    std_ulogic;

        axil_awaddr  : in  std_logic_vector(USB_DEVICE_REGS_MIN_ADDR_WIDTH - 1 downto 0);
        axil_awprot  : in  std_logic_vector(2 downto 0);
        axil_awvalid : in  std_logic;
        axil_awready : out std_logic;
        axil_wdata   : in  std_logic_vector(31 downto 0);
        axil_wstrb   : in  std_logic_vector(3 downto 0);
        axil_wvalid  : in  std_logic;
        axil_wready  : out std_logic;
        axil_bresp   : out std_logic_vector(1 downto 0);
        axil_bvalid  : out std_logic;
        axil_bready  : in  std_logic;
        axil_araddr  : in  std_logic_vector(USB_DEVICE_REGS_MIN_ADDR_WIDTH - 1 downto 0);
        axil_arprot  : in  std_logic_vector(2 downto 0);
        axil_arvalid : in  std_logic;
        axil_arready : out std_logic;
        axil_rdata   : out std_logic_vector(31 downto 0);
        axil_rresp   : out std_logic_vector(1 downto 0);
        axil_rvalid  : out std_logic;
        axil_rready  : in  std_logic;

        usb_vbus     : in    std_ulogic;
        usb_pu       : out   std_ulogic;
        irq          : out   std_ulogic;

        usb_dp       : inout std_logic;
        usb_dn       : inout std_logic
    );
end entity;

architecture rtl of usb_top is

    signal usb_vbus_sync : std_ulogic;
    signal sys_vbus_change : std_ulogic;
    signal sys_pull_up : std_ulogic;

    signal usb_dp_i_meta        : std_ulogic;
    signal usb_dn_i_meta        : std_ulogic;
    signal usb_dp_i             : std_ulogic;
    signal usb_dn_i             : std_ulogic;

    signal usb_dp_o             : std_ulogic;
    signal usb_dn_o             : std_ulogic;
    signal usb_oe               : std_ulogic;
    signal tx_dp                : std_ulogic;
    signal tx_dn                : std_ulogic;
    signal tx_oe                : std_ulogic;

    signal usb_line_state       : usb_line_state_t;
    signal usb_line_state_valid : std_ulogic;

    signal rx_packet_stream : stream_t(
        data(7 downto 0),
        keep(0 downto 0),
        dest(3 downto 0));

    signal rx_stuff_error       : std_ulogic;
    signal rx_sync_error        : std_ulogic;
    signal usb_reset_detected   : std_ulogic;
    signal sys_reset_detected   : std_ulogic;

    signal rx_payload : stream_t(
        data(7 downto 0),
        keep(0 downto 0),
        dest(3 downto 0));

    signal rx_packet            : packet_t;

    signal tx_packet            : packet_t;

    signal tx_payload : stream_t(
        data(7 downto 0),
        keep(0 downto 0),
        dest(3 downto 0));

    signal tx_packet_stream : stream_t(
        data(7 downto 0),
        keep(0 downto 0),
        dest(3 downto 0));

    signal tx_serial : stream_t(
        data(0 downto 0),
        keep(0 downto 0),
        dest(0 downto 0));

    signal hwif_in : usb_device_in_t;
    signal hwif_out : usb_device_out_t;

    signal sys_endpoint_config : endpoint_config_array_t(0 to 15)(base_address(RAM_ADDR_WIDTH - 1 downto 0));
    signal sys_transfer_length : vector_array_t(0 to 15)(9 downto 0);
    signal sys_arm : std_ulogic_vector(15 downto 0);
    signal sys_reset_toggle : std_ulogic_vector(15 downto 0);
    signal sys_event : transfer_event_t;
    signal endpoint_event_en : std_ulogic_vector(15 downto 0);
    signal endpoint_done : std_ulogic_vector(15 downto 0);
    signal partner_done : std_ulogic_vector(15 downto 0);
    signal sys_paired : std_ulogic_vector(15 downto 0);
    signal transfer_done : std_ulogic;
    signal setup_endpoint_number : std_ulogic_vector(3 downto 0);
    signal setup_clear : std_ulogic_vector(15 downto 0);

    signal usb_enable_core : std_ulogic;
    signal usb_endpoint_config : endpoint_config_array_t(0 to 15)(base_address(RAM_ADDR_WIDTH - 1 downto 0));
    signal usb_transfer_length : vector_array_t(0 to 15)(9 downto 0);
    signal usb_arm : std_ulogic_vector(15 downto 0);
    signal usb_event : transfer_event_t;
    signal usb_event_ready : std_ulogic;
    signal usb_device_address : std_ulogic_vector(6 downto 0);
    signal usb_reset_toggle : std_ulogic_vector(15 downto 0);
    signal usb_armed : std_ulogic_vector(15 downto 0);
    signal usb_in_busy : std_ulogic;
    signal usb_in_last : std_ulogic_vector(15 downto 0);

    signal rx_route : rx_route_t;
    signal setup_received : std_ulogic;
    signal out_result : out_result_t;
    signal in_request : packet_descriptor_t;
    signal in_result : in_result_t;

    signal sys_suspended : std_ulogic;
    signal sys_resume : std_ulogic;
    signal sys_sof : std_ulogic;
    signal sys_frame_number : std_ulogic_vector(10 downto 0);
    signal usb_attached : std_ulogic;
    signal usb_remote_wakeup : std_ulogic;
    signal usb_suspended : std_ulogic;
    signal usb_resume : std_ulogic;
    signal usb_sof : std_ulogic;
    signal usb_frame_number : std_ulogic_vector(10 downto 0);
    signal resume_drive : std_ulogic;

begin

    transfer_done <= not sys_event.overrun and not sys_event.setup;
    partner_done <= '0' & endpoint_done(15 downto 1);
    setup_endpoint_number <= hwif_out.endpoint(to_integer(unsigned(sys_event.endpoint_number))).control.endpoint_number.value;

    endpoint_regs_gen : for i in 0 to 15 generate
        sys_endpoint_config(i).base_address <= hwif_out.endpoint(i).control.base_address.value;
        sys_endpoint_config(i).enable <= hwif_out.endpoint(i).control.enable.value;
        sys_endpoint_config(i).direction <= hwif_out.endpoint(i).control.direction.value;
        sys_endpoint_config(i).endpoint_number <= hwif_out.endpoint(i).control.endpoint_number.value;
        sys_endpoint_config(i).kind <= hwif_out.endpoint(i).control.kind.value;
        sys_endpoint_config(i).size <= hwif_out.endpoint(i).control.size.value;
        sys_endpoint_config(i).stall <= hwif_out.endpoint(i).control.stall.value;
        sys_endpoint_config(i).double_buffer <= hwif_out.endpoint(i).control.double_buffer.value;
        sys_endpoint_config(i).split <= hwif_out.endpoint(i).control.split.value;
        sys_reset_toggle(i) <= hwif_out.endpoint(i).transfer.reset_toggle.value;
        sys_transfer_length(i) <= hwif_out.endpoint(i).transfer.length.value;
        sys_arm(i) <= hwif_out.endpoint(i).transfer.arm.value;

        endpoint_event_en(i) <= sys_event.valid when (unsigned(sys_event.endpoint_number) = i) else '0';
        endpoint_done(i) <= endpoint_event_en(i) and transfer_done;
        sys_paired(i) <= '1' when (
            i < 15 and
            sys_endpoint_config(i).double_buffer = '1' and
            sys_endpoint_config(i).kind /= ENDPOINT_KIND_CONTROL
        ) else '0';

        hwif_in.endpoint(i).status.next_buffer.hwset <= endpoint_done(i) and sys_paired(i);
        hwif_in.endpoint(i).status.next_buffer.hwclr <= partner_done(i) or sys_reset_detected or
            not sys_paired(i) or not sys_endpoint_config(i).enable;

        hwif_in.endpoint(i).status.armed.hwset <= sys_arm(i);
        setup_clear(i) <= sys_event.valid and sys_event.setup when
            (hwif_out.endpoint(i).control.endpoint_number.value = setup_endpoint_number) else '0';

        hwif_in.endpoint(i).control.stall.hwclr <= setup_clear(i);
        hwif_in.endpoint(i).status.armed.hwclr <= (endpoint_event_en(i) and transfer_done) or setup_clear(i);
        hwif_in.endpoint(i).status.byte_count.next_q <= sys_event.byte_count;
        hwif_in.endpoint(i).status.byte_count.we <= endpoint_event_en(i) and (transfer_done or sys_event.setup);
        hwif_in.endpoint(i).interrupt.transfer_complete.next_q <= endpoint_event_en(i) and transfer_done;
        hwif_in.endpoint(i).interrupt.zlp.next_q <= endpoint_event_en(i) and sys_event.zlp;
        hwif_in.endpoint(i).interrupt.overrun.next_q <= endpoint_event_en(i) and sys_event.overrun;
        hwif_in.endpoint(i).interrupt.crc_error.next_q <= '0';
        hwif_in.endpoint(i).interrupt.setup.next_q <= endpoint_event_en(i) and sys_event.setup;
    end generate;

    usb_cdc_inst : entity work.usb_cdc
        generic map (
            ADDR_WIDTH => RAM_ADDR_WIDTH
        )
        port map (
            clk_sys             => clk_sys,
            rst_sys_n           => rst_sys_n,
            clk_usb             => clk_usb,
            rst_usb_n           => rst_usb_n,
            sys_enable_core     => hwif_out.control.enable_core.value,
            sys_endpoint_config => sys_endpoint_config,
            sys_transfer_length => sys_transfer_length,
            sys_arm             => sys_arm,
            sys_device_address  => hwif_out.device_address.address.value,
            sys_reset_toggle    => sys_reset_toggle,
            sys_event           => sys_event,
            sys_enable_pu       => sys_pull_up,
            sys_remote_wakeup   => hwif_out.control.remote_wakeup.value,
            sys_suspended       => sys_suspended,
            sys_resume          => sys_resume,
            sys_sof             => sys_sof,
            sys_frame_number    => sys_frame_number,
            usb_enable_core     => usb_enable_core,
            usb_endpoint_config => usb_endpoint_config,
            usb_transfer_length => usb_transfer_length,
            usb_arm             => usb_arm,
            usb_device_address  => usb_device_address,
            usb_reset_toggle    => usb_reset_toggle,
            usb_event           => usb_event,
            usb_event_ready     => usb_event_ready,
            usb_attached        => usb_attached,
            usb_remote_wakeup   => usb_remote_wakeup,
            usb_suspended       => usb_suspended,
            usb_resume          => usb_resume,
            usb_sof             => usb_sof,
            usb_frame_number    => usb_frame_number
        );

    usb_endpoints_inst : entity work.usb_endpoints
        generic map (
            ADDR_WIDTH => RAM_ADDR_WIDTH
        )
        port map (
            clk_sys => clk_sys,
            rst_sys_n => rst_sys_n,
            clk_usb => clk_usb,
            rst_usb_n => rst_usb_n,
            ram_req => hwif_out.usb_ram.req,
            ram_addr => hwif_out.usb_ram.addr,
            ram_req_is_wr => hwif_out.usb_ram.req_is_wr,
            ram_wr_data => hwif_out.usb_ram.wr_data,
            ram_wr_biten => hwif_out.usb_ram.wr_biten,
            ram_rd_ack => hwif_in.usb_ram.rd_ack,
            ram_rd_data => hwif_in.usb_ram.rd_data,
            ram_wr_ack => hwif_in.usb_ram.wr_ack,
            enable_core => usb_enable_core,
            endpoint_config => usb_endpoint_config,
            transfer_length => usb_transfer_length,
            arm => usb_arm,
            descriptor => in_request,
            rx_route => rx_route,
            setup_received => setup_received,
            out_result => out_result,
            in_result => in_result,
            armed => usb_armed,
            in_last => usb_in_last,
            in_busy => usb_in_busy,
            event => usb_event,
            event_ready => usb_event_ready,
            rx_payload_tdata => rx_payload.data,
            rx_payload_tkeep => rx_payload.keep,
            rx_payload_tdest => rx_payload.dest,
            rx_payload_tlast => rx_payload.last,
            rx_payload_tvalid => rx_payload.valid,
            rx_payload_tready => rx_payload.ready,
            tx_payload_tdata => tx_payload.data,
            tx_payload_tkeep => tx_payload.keep,
            tx_payload_tdest => tx_payload.dest,
            tx_payload_tlast => tx_payload.last,
            tx_payload_tvalid => tx_payload.valid,
            tx_payload_tready => tx_payload.ready
        );

    usb_device_regs_inst : entity work.usb_device_regs
        port map (
            clk => clk_sys,
            arst_n => rst_sys_n,
            s_axil_awready => axil_awready,
            s_axil_awvalid => axil_awvalid,
            s_axil_awaddr => axil_awaddr,
            s_axil_awprot => axil_awprot,
            s_axil_wready => axil_wready,
            s_axil_wvalid => axil_wvalid,
            s_axil_wdata => axil_wdata,
            s_axil_wstrb => axil_wstrb,
            s_axil_bready => axil_bready,
            s_axil_bvalid => axil_bvalid,
            s_axil_bresp => axil_bresp,
            s_axil_arready => axil_arready,
            s_axil_arvalid => axil_arvalid,
            s_axil_araddr => axil_araddr,
            s_axil_arprot => axil_arprot,
            s_axil_rready => axil_rready,
            s_axil_rvalid => axil_rvalid,
            s_axil_rdata => axil_rdata,
            s_axil_rresp => axil_rresp,
            hwif_in => hwif_in,
            hwif_out => hwif_out
        );

    ----------------------------------------------------------------------------
    -- USB D+ and D- I/O buffer logic
    ----------------------------------------------------------------------------

    usb_vbus_sync_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk_sys,
            sig_in  => usb_vbus,
            sig_out => usb_vbus_sync
        );

    usb_vbus_edge_inst : entity work.edge_detector
        port map (
            clk    => clk_sys,
            sig_in => usb_vbus_sync,
            sig_re => open,
            sig_fe => open,
            sig_e  => sys_vbus_change
        );

    hwif_in.status.vbus_present.next_q <= usb_vbus_sync;
    hwif_in.status.suspended.next_q <= sys_suspended;
    hwif_in.status.frame_number.next_q <= sys_frame_number;
    hwif_in.bus_event.suspend.next_q <= sys_suspended;
    hwif_in.bus_event.resume.next_q <= sys_resume;
    hwif_in.bus_event.sof.next_q <= sys_sof;
    hwif_in.bus_event.vbus.next_q <= sys_vbus_change;

    irq <= hwif_out.interrupt.intr;
    sys_pull_up <= hwif_out.control.enable_pu.value and usb_vbus_sync;
    usb_pu <= sys_pull_up;

    usb_oe <= tx_oe or resume_drive;
    usb_dp_o <= '0' when resume_drive = '1' else tx_dp;
    usb_dn_o <= '1' when resume_drive = '1' else tx_dn;

    usb_dp <= usb_dp_o when usb_oe = '1' else 'Z';
    usb_dn <= usb_dn_o when usb_oe = '1' else 'Z';

    usb_dp_i_meta <= to_X01Z(usb_dp);
    usb_dn_i_meta <= to_X01Z(usb_dn);

    ----------------------------------------------------------------------------
    -- synchronize USB D+ and D- input signals to the local clock domain
    ----------------------------------------------------------------------------

    usb_dp_i_sync_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk_usb,
            sig_in  => usb_dp_i_meta,
            sig_out => usb_dp_i
        );

    usb_dn_i_sync_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk_usb,
            sig_in  => usb_dn_i_meta,
            sig_out => usb_dn_i
        );

    --==========================================================================
    -- RX logic
    --==========================================================================

    usb_phy_rx_inst : entity work.usb_phy_rx
        port map (
            clk                  => clk_usb,
            rst_n                => rst_usb_n,
            usb_dp_i             => usb_dp_i,
            usb_dn_i             => usb_dn_i,
            tx_active            => usb_oe,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid
        );

    usb_packet_deserializer_inst : entity work.usb_packet_deserializer
        port map (
            clk                  => clk_usb,
            rst_n                => rst_usb_n,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid,
            packet_tdata         => rx_packet_stream.data,
            packet_tkeep         => rx_packet_stream.keep,
            packet_tdest         => rx_packet_stream.dest,
            packet_tlast         => rx_packet_stream.last,
            packet_tvalid        => rx_packet_stream.valid,
            packet_tready        => rx_packet_stream.ready,
            stuff_error          => rx_stuff_error,
            sync_error           => rx_sync_error,
            reset_detected       => usb_reset_detected
        );

    usb_reset_detected_sync_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk_sys,
            sig_in  => usb_reset_detected,
            sig_out => sys_reset_detected
        );

    hwif_in.bus_event.bus_reset.next_q <= sys_reset_detected;
    hwif_in.device_address.address.hwclr <= sys_reset_detected;

    usb_packet_parser_inst : entity work.usb_packet_parser
        port map (
            clk            => clk_usb,
            rst_n          => rst_usb_n,
            packet_tdata   => rx_packet_stream.data,
            packet_tkeep   => rx_packet_stream.keep,
            packet_tdest   => rx_packet_stream.dest,
            packet_tlast   => rx_packet_stream.last,
            packet_tvalid  => rx_packet_stream.valid,
            packet_tready  => rx_packet_stream.ready,
            payload_tdata  => rx_payload.data,
            payload_tkeep  => rx_payload.keep,
            payload_tdest  => rx_payload.dest,
            payload_tlast  => rx_payload.last,
            payload_tvalid => rx_payload.valid,
            payload_tready => rx_payload.ready,
            packet         => rx_packet
        );

    usb_protocol_inst : entity work.usb_protocol
        generic map (
            ADDR_WIDTH => RAM_ADDR_WIDTH
        )
        port map (
            clk             => clk_usb,
            rst_n           => rst_usb_n,
            rx_packet       => rx_packet,
            reset_detected  => usb_reset_detected,
            enable_core     => usb_enable_core,
            device_address  => usb_device_address,
            endpoint_config => usb_endpoint_config,
            armed           => usb_armed,
            in_busy         => usb_in_busy,
            in_last         => usb_in_last,
            reset_toggle    => usb_reset_toggle,
            rx_route        => rx_route,
            tx_packet       => tx_packet,
            setup_received  => setup_received,
            out_result      => out_result,
            in_request      => in_request,
            in_result       => in_result
        );

    usb_bus_state_inst : entity work.usb_bus_state
        port map (
            clk            => clk_usb,
            rst_n          => rst_usb_n,
            usb_line_state => usb_line_state,
            attached       => usb_attached,
            rx_packet      => rx_packet,
            remote_wakeup  => usb_remote_wakeup,
            suspended      => usb_suspended,
            resume         => usb_resume,
            sof            => usb_sof,
            frame_number   => usb_frame_number,
            resume_drive   => resume_drive
        );

    --==========================================================================
    -- TX logic
    --==========================================================================

    usb_packet_generator_inst : entity work.usb_packet_generator
        port map (
            clk            => clk_usb,
            rst_n          => rst_usb_n,
            packet         => tx_packet,
            payload_tdata  => tx_payload.data,
            payload_tkeep  => tx_payload.keep,
            payload_tdest  => tx_payload.dest,
            payload_tlast  => tx_payload.last,
            payload_tvalid => tx_payload.valid,
            payload_tready => tx_payload.ready,
            packet_tdata   => tx_packet_stream.data,
            packet_tkeep   => tx_packet_stream.keep,
            packet_tdest   => tx_packet_stream.dest,
            packet_tlast   => tx_packet_stream.last,
            packet_tvalid  => tx_packet_stream.valid,
            packet_tready  => tx_packet_stream.ready
        );

    usb_packet_serializer_inst : entity work.usb_packet_serializer
        port map (
            clk           => clk_usb,
            rst_n         => rst_usb_n,

            packet_tdata  => tx_packet_stream.data,
            packet_tkeep  => tx_packet_stream.keep,
            packet_tdest  => tx_packet_stream.dest,
            packet_tlast  => tx_packet_stream.last,
            packet_tvalid => tx_packet_stream.valid,
            packet_tready => tx_packet_stream.ready,

            stream_tdata  => tx_serial.data,
            stream_tkeep  => tx_serial.keep,
            stream_tdest  => tx_serial.dest,
            stream_tlast  => tx_serial.last,
            stream_tvalid => tx_serial.valid,
            stream_tready => tx_serial.ready
        );

    usb_phy_tx_inst : entity work.usb_phy_tx
        port map (
            clk           => clk_usb,
            rst_n         => rst_usb_n,
            serial_tdata  => tx_serial.data,
            serial_tkeep  => tx_serial.keep,
            serial_tdest  => tx_serial.dest,
            serial_tlast  => tx_serial.last,
            serial_tvalid => tx_serial.valid,
            serial_tready => tx_serial.ready,
            usb_oe        => tx_oe,
            usb_dp        => tx_dp,
            usb_dn        => tx_dn
        );

end architecture;
