library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_packet_deserializer_tb is
    port (
        clk              : in  std_ulogic;
        rst_n            : in  std_ulogic;
        usb_dp_host      : in  std_logic;
        usb_dn_host      : in  std_logic;
        tx_active        : in  std_ulogic;
        usb_dp           : out std_logic;
        usb_dn           : out std_logic;
        packet_tdata     : out std_ulogic_vector(7 downto 0);
        packet_tkeep     : out std_ulogic_vector(0 downto 0);
        packet_tdest     : out std_ulogic_vector(3 downto 0);
        packet_tlast     : out std_ulogic;
        packet_tvalid    : out std_ulogic;
        packet_tready    : in  std_ulogic;
        stuff_error      : out std_ulogic;
        sync_error       : out std_ulogic;
        reset_detected   : out std_ulogic
    );
end entity;

architecture sim of usb_packet_deserializer_tb is
    signal usb_dp_bus           : std_logic;
    signal usb_dn_bus           : std_logic;
    signal usb_line_state       : usb_line_state_t;
    signal usb_line_state_valid : std_ulogic;
begin
    usb_dp_bus <= usb_dp_host;
    usb_dn_bus <= usb_dn_host;
    usb_dp_bus <= 'H';
    usb_dn_bus <= 'L';
    usb_dp <= usb_dp_bus;
    usb_dn <= usb_dn_bus;

    phy_rx_inst : entity work.usb_phy_rx
        port map (
            clk                  => clk,
            rst_n                => rst_n,
            usb_dp_i             => to_X01Z(usb_dp_bus),
            usb_dn_i             => to_X01Z(usb_dn_bus),
            tx_active            => tx_active,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid
        );

    deserializer_inst : entity work.usb_packet_deserializer
        port map (
            clk                  => clk,
            rst_n                => rst_n,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid,
            packet_tdata         => packet_tdata,
            packet_tkeep         => packet_tkeep,
            packet_tdest         => packet_tdest,
            packet_tlast         => packet_tlast,
            packet_tvalid        => packet_tvalid,
            packet_tready        => packet_tready,
            stuff_error          => stuff_error,
            sync_error           => sync_error,
            reset_detected       => reset_detected
        );
end architecture;