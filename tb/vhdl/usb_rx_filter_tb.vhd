library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_rx_filter_tb is
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        endpoint_size   : in std_ulogic_vector(47 downto 0);
        rx_route_entry  : in std_ulogic_vector(3 downto 0);
        rx_route_accept : in std_ulogic;
        rx_route_setup  : in std_ulogic;

        rx_payload_tdata  : in  std_ulogic_vector(7 downto 0);
        rx_payload_tkeep  : in  std_ulogic_vector(0 downto 0);
        rx_payload_tlast  : in  std_ulogic;
        rx_payload_tvalid : in  std_ulogic;
        rx_payload_tready : out std_ulogic;

        routed_tdata  : out std_ulogic_vector(7 downto 0);
        routed_tkeep  : out std_ulogic_vector(0 downto 0);
        routed_tdest  : out std_ulogic_vector(4 downto 0);
        routed_tlast  : out std_ulogic;
        routed_tvalid : out std_ulogic;
        routed_tready : in  std_ulogic
    );
end entity;

architecture sim of usb_rx_filter_tb is

    signal endpoint_config : endpoint_config_array_t(0 to 15)(base_address(9 downto 0));
    signal rx_route : rx_route_t;

begin

    endpoint_config_gen : for i in 0 to 15 generate
        endpoint_config(i).base_address <= (others => '0');
        endpoint_config(i).enable <= '1';
        endpoint_config(i).direction <= ENDPOINT_DIRECTION_OUT;
        endpoint_config(i).endpoint_number <= (others => '0');
        endpoint_config(i).kind <= ENDPOINT_KIND_CONTROL;
        endpoint_config(i).stall <= '0';
        endpoint_config(i).size <= endpoint_size(3 * i + 2 downto 3 * i);
        endpoint_config(i).double_buffer <= '0';
        endpoint_config(i).split <= "000";
    end generate;

    rx_route.entry <= rx_route_entry;
    rx_route.accept <= rx_route_accept;
    rx_route.setup <= rx_route_setup;

    usb_rx_filter_inst : entity work.usb_rx_filter
        port map (
            clk               => clk,
            rst_n             => rst_n,
            endpoint_config   => endpoint_config,
            rx_route          => rx_route,
            rx_payload_tdata  => rx_payload_tdata,
            rx_payload_tkeep  => rx_payload_tkeep,
            rx_payload_tlast  => rx_payload_tlast,
            rx_payload_tvalid => rx_payload_tvalid,
            rx_payload_tready => rx_payload_tready,
            routed_tdata      => routed_tdata,
            routed_tkeep      => routed_tkeep,
            routed_tdest      => routed_tdest,
            routed_tlast      => routed_tlast,
            routed_tvalid     => routed_tvalid,
            routed_tready     => routed_tready
        );

end architecture;
