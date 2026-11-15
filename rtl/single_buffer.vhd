library ieee;
use ieee.std_logic_1164.all;

entity single_buffer is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk    : in std_ulogic;
        rst_n  : in std_ulogic;

        idata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        ikeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        idest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        ilast  : in  std_ulogic;
        ivalid : in  std_ulogic;
        iready : out std_ulogic;

        odata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        okeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        odest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        olast  : out std_ulogic;
        ovalid : out std_ulogic;
        oready : in  std_ulogic
    );
end entity;

architecture rtl of single_buffer is

    signal cdata : std_ulogic_vector(DATA_WIDTH - 1 downto 0);
    signal ckeep : std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
    signal cdest : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal clast : std_ulogic;
    signal cvalid : std_ulogic;
    signal cready : std_ulogic;

begin

    input_buffer_inst : entity work.input_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => idata,
            ikeep  => ikeep,
            idest  => idest,
            ilast  => ilast,
            ivalid => ivalid,
            iready => iready,
            cdata  => cdata,
            ckeep  => ckeep,
            cdest  => cdest,
            clast  => clast,
            cvalid => cvalid,
            cready => cready
        );

    output_buffer_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => cdata,
            ckeep  => ckeep,
            cdest  => cdest,
            clast  => clast,
            cvalid => cvalid,
            cready => cready,
            odata  => odata,
            okeep  => okeep,
            odest  => odest,
            olast  => olast,
            ovalid => ovalid,
            oready => oready
        );

end architecture rtl;