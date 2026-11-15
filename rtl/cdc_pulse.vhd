library ieee;
use ieee.std_logic_1164.all;

entity cdc_pulse is
    generic (
        CDC_STAGES : natural := 2
    );
    port (
        clk_src   : in  std_ulogic;
        rst_src_n : in  std_ulogic;
        src_pulse : in  std_ulogic;

        clk_dst   : in  std_ulogic;
        rst_dst_n : in  std_ulogic;
        dst_pulse : out std_ulogic
    );
end entity;

architecture rtl of cdc_pulse is

    signal src_toggle : std_ulogic;
    signal dst_toggle : std_ulogic;
    signal dst_toggle_d : std_ulogic;

begin

    process (clk_src, rst_src_n)
    begin
        if (rst_src_n = '0') then
            src_toggle <= '0';
        elsif rising_edge(clk_src) then
            if (src_pulse = '1') then
                src_toggle <= not src_toggle;
            end if;
        end if;
    end process;

    cdc_sync_single_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => CDC_STAGES
        )
        port map (
            clk     => clk_dst,
            sig_in  => src_toggle,
            sig_out => dst_toggle
        );

    process (clk_dst, rst_dst_n)
    begin
        if (rst_dst_n = '0') then
            dst_toggle_d <= '0';
        elsif rising_edge(clk_dst) then
            dst_toggle_d <= dst_toggle;
        end if;
    end process;

    dst_pulse <= dst_toggle xor dst_toggle_d;

end architecture;
