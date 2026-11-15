library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity output_buffer is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk    : in  std_ulogic;
        rst_n  : in  std_ulogic;

        -- core signals
        cdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        ckeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        cdest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        clast  : in  std_ulogic;
        cvalid : in  std_ulogic;
        cready : out std_ulogic;

        -- output signals
        odata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        okeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        odest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        olast  : out std_ulogic;
        ovalid : out std_ulogic;
        oready : in  std_ulogic
    );
end output_buffer;

architecture rtl of output_buffer is

begin

    cready <= (not ovalid) or oready;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            ovalid <= '0';
            odata  <= (others => '0');
            okeep  <= (others => '0');
            odest  <= (others => '0');
            olast  <= '0';
        elsif rising_edge(clk) then
            if (cready = '1') then
                ovalid <= cvalid;
                odata <= cdata;
                okeep <= ckeep;
                odest <= cdest;
                olast <= clast;
            end if;
        end if;
    end process;

end rtl;