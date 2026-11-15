library ieee;
use ieee.std_logic_1164.all;

entity cdc_tb is
    port (
        clk_a   : in std_ulogic;
        rst_a_n : in std_ulogic;
        clk_b   : in std_ulogic;
        rst_b_n : in std_ulogic;

        pulse_src : in  std_ulogic;
        pulse_dst : out std_ulogic;

        hs_src_data  : in  std_ulogic_vector(15 downto 0);
        hs_src_valid : in  std_ulogic;
        hs_src_ready : out std_ulogic;
        hs_dst_data  : out std_ulogic_vector(15 downto 0);
        hs_dst_valid : out std_ulogic
    );
end entity;

architecture sim of cdc_tb is
begin

    cdc_pulse_inst : entity work.cdc_pulse
        port map (
            clk_src   => clk_a,
            rst_src_n => rst_a_n,
            src_pulse => pulse_src,
            clk_dst   => clk_b,
            rst_dst_n => rst_b_n,
            dst_pulse => pulse_dst
        );

    cdc_handshake_inst : entity work.cdc_handshake
        generic map (
            DATA_WIDTH => 16
        )
        port map (
            clk_src   => clk_a,
            rst_src_n => rst_a_n,
            src_data  => hs_src_data,
            src_valid => hs_src_valid,
            src_ready => hs_src_ready,
            clk_dst   => clk_b,
            rst_dst_n => rst_b_n,
            dst_data  => hs_dst_data,
            dst_valid => hs_dst_valid
        );

end architecture;
