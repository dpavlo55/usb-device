library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity input_buffer is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk     : in  std_ulogic;
        rst_n   : in  std_ulogic;

        -- input signals
        idata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        ikeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        idest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        ilast  : in  std_ulogic;
        ivalid : in  std_ulogic;
        iready : out std_ulogic;

        -- core signals
        cdata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        ckeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        cdest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        clast  : out std_ulogic;
        cvalid : out std_ulogic;
        cready : in  std_ulogic
    );
end input_buffer;

architecture rtl of input_buffer is

    signal idata_reg : std_ulogic_vector(DATA_WIDTH - 1 downto 0);
    signal ikeep_reg : std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
    signal idest_reg : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal ilast_reg : std_ulogic;
    signal ivalid_reg : std_ulogic;

begin

    iready <= not ivalid_reg;
    cvalid <= ivalid when iready = '1' else ivalid_reg;
    cdata <= idata when iready = '1' else idata_reg;
    ckeep <= ikeep when iready = '1' else ikeep_reg;
    cdest <= idest when iready = '1' else idest_reg;
    clast <= ilast when iready = '1' else ilast_reg;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            idata_reg <= (others => '0');
            ikeep_reg <= (others => '0');
            idest_reg <= (others => '0');
            ilast_reg <= '0';
            ivalid_reg <= '0';
        elsif rising_edge(clk) then
            if (cready = '1' and ivalid_reg = '1') then
                ivalid_reg <= '0';
            elsif ((not cready) = '1' and ivalid_reg = '0') then
                idata_reg <= idata;
                ikeep_reg <= ikeep;
                idest_reg <= idest;
                ilast_reg <= ilast;
                ivalid_reg <= ivalid;
            end if;
        end if;
    end process;

end rtl;