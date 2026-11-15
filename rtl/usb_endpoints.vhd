library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_endpoints is
    generic (
        ADDR_WIDTH : natural := 10;
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk_sys   : in std_ulogic;
        rst_sys_n : in std_ulogic;
        clk_usb   : in std_ulogic;
        rst_usb_n : in std_ulogic;

        ram_req       : in  std_ulogic;
        ram_addr      : in  std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
        ram_req_is_wr : in  std_ulogic;
        ram_wr_data   : in  std_ulogic_vector(31 downto 0);
        ram_wr_biten  : in  std_ulogic_vector(31 downto 0);
        ram_rd_ack    : out std_ulogic;
        ram_rd_data   : out std_ulogic_vector(31 downto 0);
        ram_wr_ack    : out std_ulogic;

        enable_core     : in  std_ulogic;
        endpoint_config : in  endpoint_config_array_t(0 to 2 ** DEST_WIDTH - 1)(
            base_address(ADDR_WIDTH - 1 downto 0));
        transfer_length : in  vector_array_t(0 to 2 ** DEST_WIDTH - 1)(9 downto 0);
        arm             : in  std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        descriptor      : in  packet_descriptor_t;
        rx_route        : in  rx_route_t;
        setup_received  : in  std_ulogic;
        out_result      : in  out_result_t;
        in_result       : in  in_result_t;
        armed           : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        in_last         : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        in_busy         : out std_ulogic;
        event           : out transfer_event_t;
        event_ready     : in  std_ulogic;

        rx_payload_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        rx_payload_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        rx_payload_tdest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        rx_payload_tlast  : in  std_ulogic;
        rx_payload_tvalid : in  std_ulogic;
        rx_payload_tready : out std_ulogic;

        tx_payload_tdata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        tx_payload_tkeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        tx_payload_tdest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        tx_payload_tlast  : out std_ulogic;
        tx_payload_tvalid : out std_ulogic;
        tx_payload_tready : in  std_ulogic
    );
end entity usb_endpoints;

architecture rtl of usb_endpoints is

    signal bank_en : std_ulogic;
    signal bank_we : std_ulogic_vector(3 downto 0);
    signal bank_addr : std_ulogic_vector(ADDR_WIDTH - 3 downto 0);
    signal bank_wdata : std_ulogic_vector(31 downto 0);
    signal bank_rdata : std_ulogic_vector(31 downto 0);

    signal usb_ram_en : std_ulogic;
    signal usb_ram_we : std_ulogic;
    signal usb_ram_addr : std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
    signal usb_ram_wdata : std_ulogic_vector(DATA_WIDTH - 1 downto 0);
    signal usb_ram_rdata : std_ulogic_vector(DATA_WIDTH - 1 downto 0);

    signal endpoint_base_address : vector_array_t(0 to 2 ** DEST_WIDTH - 1)(ADDR_WIDTH - 1 downto 0);
    signal write_allowed : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal read_command : ram_read_command_t(base_address(ADDR_WIDTH - 1 downto 0));
    signal in_report : packet_report_t;

    signal routed : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH downto 0));
    signal write : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal setup_writing : std_ulogic;
    signal setup_entry : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal setup_written : std_ulogic;

begin

    usb_rx_filter_inst : entity work.usb_rx_filter
        generic map (
            ADDR_WIDTH => ADDR_WIDTH,
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk               => clk_usb,
            rst_n             => rst_usb_n,
            endpoint_config   => endpoint_config,
            rx_route          => rx_route,
            rx_payload_tdata  => rx_payload_tdata,
            rx_payload_tkeep  => rx_payload_tkeep,
            rx_payload_tlast  => rx_payload_tlast,
            rx_payload_tvalid => rx_payload_tvalid,
            rx_payload_tready => rx_payload_tready,
            routed_tdata      => routed.data,
            routed_tkeep      => routed.keep,
            routed_tdest      => routed.dest,
            routed_tlast      => routed.last,
            routed_tvalid     => routed.valid,
            routed_tready     => routed.ready
        );

    usb_setup_stage_inst : entity work.usb_setup_stage
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk            => clk_usb,
            rst_n          => rst_usb_n,
            routed_tdata   => routed.data,
            routed_tkeep   => routed.keep,
            routed_tdest   => routed.dest,
            routed_tlast   => routed.last,
            routed_tvalid  => routed.valid,
            routed_tready  => routed.ready,
            setup_received => setup_received,
            setup_writing  => setup_writing,
            setup_entry    => setup_entry,
            setup_written  => setup_written,
            write_tdata    => write.data,
            write_tkeep    => write.keep,
            write_tdest    => write.dest,
            write_tlast    => write.last,
            write_tvalid   => write.valid,
            write_tready   => write.ready
        );

    usb_ram_sw_adapter_inst : entity work.usb_ram_sw_adapter
        generic map (
            ADDR_WIDTH => ADDR_WIDTH
        )
        port map (
            clk           => clk_sys,
            rst_n         => rst_sys_n,
            ram_req       => ram_req,
            ram_addr      => ram_addr,
            ram_req_is_wr => ram_req_is_wr,
            ram_wr_data   => ram_wr_data,
            ram_wr_biten  => ram_wr_biten,
            ram_rd_ack    => ram_rd_ack,
            ram_rd_data   => ram_rd_data,
            ram_wr_ack    => ram_wr_ack,
            bank_en       => bank_en,
            bank_we       => bank_we,
            bank_addr     => bank_addr,
            bank_wdata    => bank_wdata,
            bank_rdata    => bank_rdata
        );

    usb_ram_inst : entity work.usb_ram
        generic map (
            ADDR_WIDTH => ADDR_WIDTH
        )
        port map (
            clk_sys   => clk_sys,
            en_sys    => bank_en,
            we_sys    => bank_we,
            addr_sys  => bank_addr,
            idata_sys => bank_wdata,
            odata_sys => bank_rdata,
            clk_usb   => clk_usb,
            en_usb    => usb_ram_en,
            we_usb    => usb_ram_we,
            addr_usb  => usb_ram_addr,
            idata_usb => usb_ram_wdata,
            odata_usb => usb_ram_rdata
        );

    usb_endpoints_controller_inst : entity work.usb_endpoints_controller
        generic map (
            ADDR_WIDTH => ADDR_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk                   => clk_usb,
            rst_n                 => rst_usb_n,
            enable_core           => enable_core,
            endpoint_config       => endpoint_config,
            transfer_length       => transfer_length,
            arm                   => arm,
            descriptor            => descriptor,
            setup_writing         => setup_writing,
            setup_entry           => setup_entry,
            setup_written         => setup_written,
            out_result            => out_result,
            in_result             => in_result,
            endpoint_base_address => endpoint_base_address,
            write_allowed         => write_allowed,
            read_command          => read_command,
            in_report             => in_report,
            armed                 => armed,
            in_last               => in_last,
            in_busy               => in_busy,
            event                 => event,
            event_ready           => event_ready
        );

    usb_ram_usb_adapter_inst : entity work.usb_ram_usb_adapter
        generic map (
            ADDR_WIDTH => ADDR_WIDTH,
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk                   => clk_usb,
            rst_n                 => rst_usb_n,
            endpoint_base_address => endpoint_base_address,
            write_allowed         => write_allowed,
            read_command          => read_command,
            out_report            => open,
            in_report             => in_report,
            rx_payload_tdata      => write.data,
            rx_payload_tkeep      => write.keep,
            rx_payload_tdest      => write.dest,
            rx_payload_tlast      => write.last,
            rx_payload_tvalid     => write.valid,
            rx_payload_tready     => write.ready,
            tx_payload_tdata      => tx_payload_tdata,
            tx_payload_tkeep      => tx_payload_tkeep,
            tx_payload_tdest      => tx_payload_tdest,
            tx_payload_tlast      => tx_payload_tlast,
            tx_payload_tvalid     => tx_payload_tvalid,
            tx_payload_tready     => tx_payload_tready,
            ram_en                => usb_ram_en,
            ram_we                => usb_ram_we,
            ram_addr              => usb_ram_addr,
            ram_wdata             => usb_ram_wdata,
            ram_rdata             => usb_ram_rdata
        );

end architecture rtl;
