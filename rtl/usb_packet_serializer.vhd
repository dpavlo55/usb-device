library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.stream_pkg.all;

entity usb_packet_serializer is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        packet_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        packet_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        packet_tdest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        packet_tlast  : in  std_ulogic;
        packet_tvalid : in  std_ulogic;
        packet_tready : out std_ulogic;

        stream_tdata  : out std_ulogic_vector(0 downto 0);
        stream_tkeep  : out std_ulogic_vector(0 downto 0);
        stream_tdest  : out std_ulogic_vector(0 downto 0);
        stream_tlast  : out std_ulogic;
        stream_tvalid : out std_ulogic;
        stream_tready : in  std_ulogic
    );
end entity usb_packet_serializer;

architecture rtl of usb_packet_serializer is

    constant LAST_BIT_INDEX_IN_BYTE : natural := DATA_WIDTH - 1;

    signal packet : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal fifo : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal stream : stream_t(
        data(0 downto 0),
        keep(0 downto 0),
        dest(0 downto 0));

    signal packet_fire : std_ulogic;
    signal stream_fire : std_ulogic;

    signal full : std_ulogic;

    signal bit_cnt : unsigned(2 downto 0);
    signal last_bit_in_byte : std_ulogic;

begin

    input_buffer_inst : entity work.input_buffer
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => packet_tdata,
            ikeep  => packet_tkeep,
            idest  => packet_tdest,
            ilast  => packet_tlast,
            ivalid => packet_tvalid,
            iready => packet_tready,
            cdata  => packet.data,
            ckeep  => packet.keep,
            cdest  => packet.dest,
            clast  => packet.last,
            cvalid => packet.valid,
            cready => packet.ready
        );

    packet_fire <= packet.valid and packet.ready;
    stream_fire <= stream.valid and stream.ready;

    process (clk, rst_n)
    begin
        if rising_edge(clk) then
            if (packet_fire = '1') then
                fifo <= packet;
            end if;
        end if;
    end process;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            full <= '0';
        elsif rising_edge(clk) then
            if (packet_fire = '1') then
                full <= '1';
            elsif (stream_fire = '1' and last_bit_in_byte = '1') then
                full <= '0';
            end if;
        end if;
    end process;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            bit_cnt <= (others => '0');
        elsif rising_edge(clk) then
            if (stream_fire = '1') then
                bit_cnt <= bit_cnt + 1;
            end if;
        end if;
    end process;

    last_bit_in_byte <= '1' when (bit_cnt = LAST_BIT_INDEX_IN_BYTE) else '0';

    packet.ready <= (not full) or (full and stream_fire and last_bit_in_byte);
    stream.valid <= full;

    stream.data(0) <= fifo.data(to_integer(bit_cnt));
    stream.keep(0) <= fifo.keep(0);
    stream.dest(0) <= fifo.dest(0);
    stream.last <= fifo.last and last_bit_in_byte;

    output_buffer_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => 1,
            KEEP_WIDTH => 1,
            DEST_WIDTH => 1
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => stream.data,
            ckeep  => stream.keep,
            cdest  => stream.dest,
            clast  => stream.last,
            cvalid => stream.valid,
            cready => stream.ready,
            odata  => stream_tdata,
            okeep  => stream_tkeep,
            odest  => stream_tdest,
            olast  => stream_tlast,
            ovalid => stream_tvalid,
            oready => stream_tready
        );

end architecture rtl;