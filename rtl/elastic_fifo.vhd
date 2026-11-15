library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.stream_pkg.all;

entity elastic_fifo is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4;
        ADDR_WIDTH : natural := 2
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

architecture rtl of elastic_fifo is

    constant FIFO_DEPTH : natural := 2 ** ADDR_WIDTH;

    type fifo_t is array (0 to FIFO_DEPTH - 1) of std_ulogic_vector(
        (DATA_WIDTH + KEEP_WIDTH + DEST_WIDTH + 1) - 1 downto 0);

    signal fifo : fifo_t;

    signal write_enable : std_ulogic;
    signal read_enable : std_ulogic;

    signal empty : std_ulogic;
    signal full : std_ulogic;

    signal write_pointer : unsigned(ADDR_WIDTH downto 0);
    signal read_pointer : unsigned(ADDR_WIDTH downto 0);

    signal write_address : natural;
    signal read_address : natural;

    signal istream : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));
    signal ostream : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

begin

    input_buffer_inst : entity work.input_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk => clk,
            rst_n => rst_n,
            idata => idata,
            ikeep => ikeep,
            idest => idest,
            ilast => ilast,
            ivalid => ivalid,
            iready => iready,
            cdata => istream.data,
            ckeep => istream.keep,
            cdest => istream.dest,
            clast => istream.last,
            cvalid => istream.valid,
            cready => istream.ready
        );

    output_buffer_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk => clk,
            rst_n => rst_n,
            cdata => ostream.data,
            ckeep => ostream.keep,
            cdest => ostream.dest,
            clast => ostream.last,
            cvalid => ostream.valid,
            cready => ostream.ready,
            odata => odata,
            okeep => okeep,
            odest => odest,
            olast => olast,
            ovalid => ovalid,
            oready => oready
        );

    write_enable <= istream.valid and istream.ready;
    read_enable <= ostream.valid and ostream.ready;

    empty <= '1' when (write_pointer = read_pointer) else '0';
    full <= '1' when
        ((not write_pointer(ADDR_WIDTH) &
        write_pointer(ADDR_WIDTH - 1 downto 0)) = read_pointer) else '0';

    istream.ready <= not full;
    ostream.valid <= not empty;

    write_address <= to_integer(write_pointer(ADDR_WIDTH - 1 downto 0));
    read_address <= to_integer(read_pointer(ADDR_WIDTH - 1 downto 0));

    -- write process
    process (clk, rst_n)
    begin
        if rising_edge(clk) then
            if (write_enable = '1') then
                fifo(write_address) <=
                    istream.last & istream.dest & istream.keep & istream.data;
            end if;
        end if;
    end process;

    (ostream.last, ostream.dest, ostream.keep, ostream.data) <= fifo(read_address);

    -- pointers process
    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            write_pointer <= (others => '0');
            read_pointer <= (others => '0');
        elsif rising_edge(clk) then
            if (write_enable = '1') then
                write_pointer <= write_pointer + 1;
            end if;
            if (read_enable = '1') then
                read_pointer <= read_pointer + 1;
            end if;
        end if;
    end process;

end architecture rtl;