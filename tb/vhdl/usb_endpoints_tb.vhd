library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_endpoints_tb is
    port (
        clk_sys   : in std_ulogic;
        rst_sys_n : in std_ulogic;
        clk_usb   : in std_ulogic;
        rst_usb_n : in std_ulogic;

        enable_core           : in std_ulogic;
        endpoint_base_address : in std_ulogic_vector(16 * 10 - 1 downto 0);
        endpoint_enable       : in std_ulogic_vector(15 downto 0);
        endpoint_direction    : in std_ulogic_vector(15 downto 0);
        endpoint_number       : in std_ulogic_vector(63 downto 0);
        endpoint_stall        : in std_ulogic_vector(15 downto 0);
        endpoint_kind         : in std_ulogic_vector(31 downto 0);
        endpoint_size         : in std_ulogic_vector(47 downto 0);
        endpoint_split        : in std_ulogic_vector(47 downto 0);
        reset_toggle          : in std_ulogic_vector(15 downto 0);
        usb_endpoint_stall    : out std_ulogic_vector(15 downto 0);
        usb_reset_toggle      : out std_ulogic_vector(15 downto 0);
        usb_armed             : out std_ulogic_vector(15 downto 0);
        usb_in_last           : out std_ulogic_vector(15 downto 0);
        sys_device_address    : in std_ulogic_vector(6 downto 0);
        usb_device_address    : out std_ulogic_vector(6 downto 0);
        transfer_length       : in std_ulogic_vector(16 * 10 - 1 downto 0);
        arm                   : in std_ulogic_vector(15 downto 0);

        event_endpoint   : out std_ulogic_vector(3 downto 0);
        event_byte_count : out std_ulogic_vector(9 downto 0);
        event_zlp        : out std_ulogic;
        event_overrun    : out std_ulogic;
        event_setup      : out std_ulogic;
        event_valid      : out std_ulogic;

        ram_req       : in  std_ulogic;
        ram_addr      : in  std_ulogic_vector(9 downto 0);
        ram_req_is_wr : in  std_ulogic;
        ram_wr_data   : in  std_ulogic_vector(31 downto 0);
        ram_wr_biten  : in  std_ulogic_vector(31 downto 0);
        ram_rd_ack    : out std_ulogic;
        ram_rd_data   : out std_ulogic_vector(31 downto 0);
        ram_wr_ack    : out std_ulogic;

        descriptor_endpoint_number : in std_ulogic_vector(3 downto 0);
        descriptor_start           : in std_ulogic;

        rx_route_entry   : in  std_ulogic_vector(3 downto 0);
        rx_route_accept  : in  std_ulogic;
        rx_route_setup   : in  std_ulogic;
        setup_received   : in  std_ulogic;
        out_complete     : in  std_ulogic;
        out_overrun      : in  std_ulogic;
        out_entry        : in  std_ulogic_vector(3 downto 0);
        out_byte_count   : in  std_ulogic_vector(9 downto 0);
        in_complete      : in  std_ulogic;
        in_complete_entry : in  std_ulogic_vector(3 downto 0);

        rx_payload_tdata  : in  std_ulogic_vector(7 downto 0);
        rx_payload_tkeep  : in  std_ulogic_vector(0 downto 0);
        rx_payload_tdest  : in  std_ulogic_vector(3 downto 0);
        rx_payload_tlast  : in  std_ulogic;
        rx_payload_tvalid : in  std_ulogic;
        rx_payload_tready : out std_ulogic;

        tx_payload_tdata  : out std_ulogic_vector(7 downto 0);
        tx_payload_tkeep  : out std_ulogic_vector(0 downto 0);
        tx_payload_tdest  : out std_ulogic_vector(3 downto 0);
        tx_payload_tlast  : out std_ulogic;
        tx_payload_tvalid : out std_ulogic;
        tx_payload_tready : in  std_ulogic
    );
end entity;

architecture sim of usb_endpoints_tb is

    signal sys_endpoint_config : endpoint_config_array_t(0 to 15)(base_address(9 downto 0));
    signal sys_transfer_length : vector_array_t(0 to 15)(9 downto 0);
    signal sys_event : transfer_event_t;

    signal usb_enable_core : std_ulogic;
    signal usb_endpoint_config : endpoint_config_array_t(0 to 15)(base_address(9 downto 0));
    signal usb_transfer_length : vector_array_t(0 to 15)(9 downto 0);
    signal usb_arm : std_ulogic_vector(15 downto 0);
    signal usb_event : transfer_event_t;
    signal usb_event_ready : std_ulogic;

    signal descriptor : packet_descriptor_t;
    signal rx_route : rx_route_t;
    signal out_result : out_result_t;
    signal in_result : in_result_t;

begin

    endpoint_gen : for i in 0 to 15 generate
        sys_endpoint_config(i).base_address <= endpoint_base_address(10 * i + 9 downto 10 * i);
        sys_endpoint_config(i).enable <= endpoint_enable(i);
        sys_endpoint_config(i).direction <= endpoint_direction(i);
        sys_endpoint_config(i).endpoint_number <= endpoint_number(4 * i + 3 downto 4 * i);
        sys_endpoint_config(i).kind <= endpoint_kind(2 * i + 1 downto 2 * i);
        sys_endpoint_config(i).stall <= endpoint_stall(i);
        sys_endpoint_config(i).size <= endpoint_size(3 * i + 2 downto 3 * i);
        sys_endpoint_config(i).double_buffer <= '0';
        sys_endpoint_config(i).split <= endpoint_split(3 * i + 2 downto 3 * i);
        usb_endpoint_stall(i) <= usb_endpoint_config(i).stall;
        sys_transfer_length(i) <= transfer_length(10 * i + 9 downto 10 * i);
    end generate;

    event_endpoint <= sys_event.endpoint_number;
    event_byte_count <= sys_event.byte_count;
    event_zlp <= sys_event.zlp;
    event_overrun <= sys_event.overrun;
    event_setup <= sys_event.setup;
    event_valid <= sys_event.valid;

    descriptor.endpoint_number <= descriptor_endpoint_number;
    descriptor.start <= descriptor_start;

    rx_route.entry <= rx_route_entry;
    rx_route.accept <= rx_route_accept;
    rx_route.setup <= rx_route_setup;

    out_result.complete <= out_complete;
    out_result.overrun <= out_overrun;
    out_result.entry <= out_entry;
    out_result.byte_count <= out_byte_count;

    in_result.complete <= in_complete;
    in_result.entry <= in_complete_entry;

    usb_cdc_inst : entity work.usb_cdc
        port map (
            clk_sys             => clk_sys,
            rst_sys_n           => rst_sys_n,
            clk_usb             => clk_usb,
            rst_usb_n           => rst_usb_n,
            sys_enable_core     => enable_core,
            sys_endpoint_config => sys_endpoint_config,
            sys_device_address  => sys_device_address,
            sys_reset_toggle    => reset_toggle,
            sys_transfer_length => sys_transfer_length,
            sys_arm             => arm,
            sys_event           => sys_event,
            sys_enable_pu       => '0',
            sys_remote_wakeup   => '0',
            sys_suspended       => open,
            sys_resume          => open,
            sys_sof             => open,
            sys_frame_number    => open,
            usb_enable_core     => usb_enable_core,
            usb_endpoint_config => usb_endpoint_config,
            usb_device_address  => usb_device_address,
            usb_reset_toggle    => usb_reset_toggle,
            usb_transfer_length => usb_transfer_length,
            usb_arm             => usb_arm,
            usb_event           => usb_event,
            usb_event_ready     => usb_event_ready,
            usb_attached        => open,
            usb_remote_wakeup   => open,
            usb_suspended       => '0',
            usb_resume          => '0',
            usb_sof             => '0',
            usb_frame_number    => (others => '0')
        );

    usb_endpoints_inst : entity work.usb_endpoints
        port map (
            clk_sys           => clk_sys,
            rst_sys_n         => rst_sys_n,
            clk_usb           => clk_usb,
            rst_usb_n         => rst_usb_n,
            ram_req           => ram_req,
            ram_addr          => ram_addr,
            ram_req_is_wr     => ram_req_is_wr,
            ram_wr_data       => ram_wr_data,
            ram_wr_biten      => ram_wr_biten,
            ram_rd_ack        => ram_rd_ack,
            ram_rd_data       => ram_rd_data,
            ram_wr_ack        => ram_wr_ack,
            enable_core       => usb_enable_core,
            endpoint_config   => usb_endpoint_config,
            transfer_length   => usb_transfer_length,
            arm               => usb_arm,
            descriptor        => descriptor,
            rx_route          => rx_route,
            setup_received    => setup_received,
            out_result        => out_result,
            in_result         => in_result,
            armed             => usb_armed,
            in_last           => usb_in_last,
            event             => usb_event,
            event_ready       => usb_event_ready,
            rx_payload_tdata  => rx_payload_tdata,
            rx_payload_tkeep  => rx_payload_tkeep,
            rx_payload_tdest  => rx_payload_tdest,
            rx_payload_tlast  => rx_payload_tlast,
            rx_payload_tvalid => rx_payload_tvalid,
            rx_payload_tready => rx_payload_tready,
            tx_payload_tdata  => tx_payload_tdata,
            tx_payload_tkeep  => tx_payload_tkeep,
            tx_payload_tdest  => tx_payload_tdest,
            tx_payload_tlast  => tx_payload_tlast,
            tx_payload_tvalid => tx_payload_tvalid,
            tx_payload_tready => tx_payload_tready
        );

end architecture;
