library ieee;
use ieee.std_logic_1164.all;

entity cdc_sync_single is
    generic (
        CDC_STAGES : natural := 2
    );
    port (
        clk     : in  std_ulogic;
        sig_in  : in  std_ulogic;
        sig_out : out std_ulogic
    );
end entity;

architecture rtl of cdc_sync_single is

    signal synchronizer_chain : std_ulogic_vector(CDC_STAGES - 1 downto 0);

begin

    assert CDC_STAGES >= 2
        report "CDC_STAGES must be greater than or equal to two"
        severity failure;

    process (clk)
    begin
        if rising_edge(clk) then
            synchronizer_chain(0) <= sig_in;

            for stage_index in 1 to CDC_STAGES - 1 loop
                synchronizer_chain(stage_index) <= synchronizer_chain(stage_index - 1);
            end loop;
        end if;
    end process;

    sig_out <= synchronizer_chain(CDC_STAGES - 1);

end architecture;