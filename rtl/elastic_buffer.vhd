library ieee;
use ieee.std_logic_1164.all;

entity elastic_buffer is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4;
        DEPTH : natural := 2
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

architecture rtl of elastic_buffer is

    type stream_t is record
        data  : std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        keep  : std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        dest  : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        last  : std_ulogic;
        valid : std_ulogic;
        ready : std_ulogic;
    end record;

    type stream_array_t is array (0 to DEPTH) of stream_t;

    signal stream : stream_array_t;

begin

    stream(0).data  <= idata;
    stream(0).keep  <= ikeep;
    stream(0).dest  <= idest;
    stream(0).last  <= ilast;
    stream(0).valid <= ivalid;
    iready <= stream(0).ready;

    odata <= stream(DEPTH).data;
    okeep <= stream(DEPTH).keep;
    odest <= stream(DEPTH).dest;
    olast <= stream(DEPTH).last;
    ovalid <= stream(DEPTH).valid;
    stream(DEPTH).ready <= oready;

    gen : for i in 0 to DEPTH - 1 generate
        single_buffer_inst : entity work.single_buffer
            generic map (
                DATA_WIDTH => DATA_WIDTH,
                KEEP_WIDTH => KEEP_WIDTH,
                DEST_WIDTH => DEST_WIDTH
            )
            port map (
                clk => clk,
                rst_n => rst_n,

                idata  => stream(i).data,
                ikeep  => stream(i).keep,
                idest  => stream(i).dest,
                ilast  => stream(i).last,
                ivalid => stream(i).valid,
                iready => stream(i).ready,

                odata  => stream(i + 1).data,
                okeep  => stream(i + 1).keep,
                odest  => stream(i + 1).dest,
                olast  => stream(i + 1).last,
                ovalid => stream(i + 1).valid,
                oready => stream(i + 1).ready
            );
    end generate;

end architecture rtl;
