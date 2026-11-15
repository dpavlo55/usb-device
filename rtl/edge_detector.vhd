library ieee;
use ieee.std_logic_1164.all;

entity edge_detector is
    generic (
        REG_EN : boolean := false
    );
    port (
        clk    : in  std_ulogic;
        sig_in : in  std_ulogic;
        sig_re : out std_ulogic;
        sig_fe : out std_ulogic;
        sig_e  : out std_ulogic
    );
end entity;

architecture rtl of edge_detector is

    signal sig_d    : std_ulogic;
    signal sig_re_comb : std_ulogic;
    signal sig_fe_comb : std_ulogic;
    signal sig_e_comb  : std_ulogic;

begin

    process (clk)
    begin
        if rising_edge(clk) then
            sig_d <= sig_in;
        end if;
    end process;

    sig_re_comb <= sig_in and not sig_d;
    sig_fe_comb <= not sig_in and sig_d;
    sig_e_comb  <= sig_re_comb or sig_fe_comb;

    output_logic : if REG_EN generate
        process (clk)
        begin
            if rising_edge(clk) then
                sig_re <= sig_re_comb;
                sig_fe <= sig_fe_comb;
                sig_e  <= sig_e_comb;
            end if;
        end process;
    else generate
        sig_re <= sig_re_comb;
        sig_fe <= sig_fe_comb;
        sig_e  <= sig_e_comb;
    end generate;

end architecture;