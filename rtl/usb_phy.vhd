library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;

entity usb_phy is
    port (
        clk           : in    std_ulogic;
        rst_n         : in    std_ulogic;

        usb_dp_io     : inout std_logic;
        usb_dn_io     : inout std_logic;

        m_axis_tvalid : out   std_ulogic;
        m_axis_tready : in    std_ulogic;
        m_axis_tdata  : out   std_ulogic_vector(7 downto 0);
        m_axis_tlast  : out   std_ulogic;
        m_axis_tdest  : out   std_ulogic_vector(3 downto 0);
        m_axis_tuser  : out   std_ulogic_vector(7 downto 0);

        stuff_error   : out   std_ulogic;
        sync_error    : out   std_ulogic
    );
end entity;

architecture rtl of usb_phy is

    signal usb_dp_i_meta           : std_ulogic;
    signal usb_dn_i_meta           : std_ulogic;
    signal usb_dp_i                : std_ulogic;
    signal usb_dn_i                : std_ulogic;

    signal usb_dp_o                : std_ulogic;
    signal usb_dn_o                : std_ulogic;

    signal usb_oe                  : std_ulogic;

    signal usb_line_state          : usb_line_state_t;
    signal usb_line_state_valid    : std_ulogic;

begin

    ----------------------------------------------------------------------------
    -- USB D+ and D- I/O buffer logic
    ----------------------------------------------------------------------------

    usb_oe <= '0';
    usb_dp_o <= '1';
    usb_dn_o <= '0';

    usb_dp_io <= usb_dp_o when usb_oe = '1' else 'Z';
    usb_dn_io <= usb_dn_o when usb_oe = '1' else 'Z';

    usb_dp_i_meta <= to_X01Z(usb_dp_io);
    usb_dn_i_meta <= to_X01Z(usb_dn_io);

    ----------------------------------------------------------------------------
    -- synchronize USB D+ and D- input signals to the local clock domain
    ----------------------------------------------------------------------------

    usb_dp_i_sync : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk,
            sig_in  => usb_dp_i_meta,
            sig_out => usb_dp_i
        );

    usb_dn_i_sync : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk,
            sig_in  => usb_dn_i_meta,
            sig_out => usb_dn_i
        );

    --==========================================================================
    -- RX logic
    --==========================================================================

    usb_phy_rx_inst : entity work.usb_phy_rx
        port map (
            clk                  => clk,
            rst_n                => rst_n,
            usb_dp_i             => usb_dp_i,
            usb_dn_i             => usb_dn_i,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid
        );

    usb_packet_deserializer_inst : entity work.usb_packet_deserializer
        port map (
            clk                  => clk,
            rst_n                => rst_n,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid,
            m_axis_tvalid        => m_axis_tvalid,
            m_axis_tready        => m_axis_tready,
            m_axis_tdata         => m_axis_tdata,
            m_axis_tlast         => m_axis_tlast,
            m_axis_tdest         => m_axis_tdest,
            m_axis_tuser         => m_axis_tuser,
            stuff_error          => stuff_error,
            sync_error           => sync_error
        );

end architecture;