library ieee;
use ieee.std_logic_1164.all;

entity usb_phy_tx_tb is
    port (
        clk           : in  std_ulogic;
        rst_n         : in  std_ulogic;

        serial_tdata  : in  std_ulogic_vector(0 downto 0);
        serial_tkeep  : in  std_ulogic_vector(0 downto 0);
        serial_tdest  : in  std_ulogic_vector(0 downto 0);
        serial_tlast  : in  std_ulogic;
        serial_tvalid : in  std_ulogic;
        serial_tready : out std_ulogic;

        usb_oe        : out std_ulogic;
        usb_dp        : out std_ulogic;
        usb_dn        : out std_ulogic
    );
end entity usb_phy_tx_tb;

architecture sim of usb_phy_tx_tb is
begin
    phy_tx_inst : entity work.usb_phy_tx
        generic map (
            DATA_WIDTH => 1,
            KEEP_WIDTH => 1,
            DEST_WIDTH => 1
        )
        port map (
            clk           => clk,
            rst_n         => rst_n,
            serial_tdata  => serial_tdata,
            serial_tkeep  => serial_tkeep,
            serial_tdest  => serial_tdest,
            serial_tlast  => serial_tlast,
            serial_tvalid => serial_tvalid,
            serial_tready => serial_tready,
            usb_oe        => usb_oe,
            usb_dp        => usb_dp,
            usb_dn        => usb_dn
        );
end architecture sim;
