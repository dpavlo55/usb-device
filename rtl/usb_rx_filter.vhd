library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_rx_filter is
    generic (
        ADDR_WIDTH : natural := 10;
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        endpoint_config : in endpoint_config_array_t(0 to 2 ** DEST_WIDTH - 1)(
            base_address(ADDR_WIDTH - 1 downto 0));
        rx_route        : in rx_route_t;

        rx_payload_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        rx_payload_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        rx_payload_tlast  : in  std_ulogic;
        rx_payload_tvalid : in  std_ulogic;
        rx_payload_tready : out std_ulogic;

        routed_tdata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        routed_tkeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        routed_tdest  : out std_ulogic_vector(DEST_WIDTH downto 0);
        routed_tlast  : out std_ulogic;
        routed_tvalid : out std_ulogic;
        routed_tready : in  std_ulogic
    );
end entity usb_rx_filter;

architecture rtl of usb_rx_filter is

    constant ROUTE_WIDTH : natural := DEST_WIDTH + 2;

    signal route_in : std_ulogic_vector(ROUTE_WIDTH - 1 downto 0);

    signal rx : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(ROUTE_WIDTH - 1 downto 0));
    signal routed : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH downto 0));

    signal first : std_ulogic;
    signal route : std_ulogic_vector(ROUTE_WIDTH - 1 downto 0);
    signal route_held : std_ulogic_vector(ROUTE_WIDTH - 1 downto 0);
    signal route_accept : std_ulogic;
    signal route_entry : natural range 0 to 2 ** DEST_WIDTH - 1;
    signal rx_fire : std_ulogic;

    signal byte_count : unsigned(10 downto 0);
    signal byte_count_held : unsigned(10 downto 0);
    signal buffer_bytes : unsigned(10 downto 0);
    signal in_buffer : std_ulogic;

begin

    -- The route travels with every beat into the input buffer, so the route of
    -- the cycle a first beat is taken is the one used for its packet.
    route_in <= rx_route.accept & rx_route.setup & rx_route.entry(DEST_WIDTH - 1 downto 0);

    input_buffer_inst : entity work.input_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => ROUTE_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => rx_payload_tdata,
            ikeep  => rx_payload_tkeep,
            idest  => route_in,
            ilast  => rx_payload_tlast,
            ivalid => rx_payload_tvalid,
            iready => rx_payload_tready,
            cdata  => rx.data,
            ckeep  => rx.keep,
            cdest  => rx.dest,
            clast  => rx.last,
            cvalid => rx.valid,
            cready => rx.ready
        );

    route <= rx.dest when (first = '1') else route_held;
    route_accept <= route(ROUTE_WIDTH - 1);
    route_entry <= to_integer(unsigned(route(DEST_WIDTH - 1 downto 0)));

    -- Bytes past the entry's buffer (2 ** (size + 3) bytes) lose keep: they are
    -- neither written nor counted, and last still ends the packet.
    byte_count <= (others => '0') when (first = '1') else byte_count_held;
    buffer_bytes <= shift_left(to_unsigned(8, 11), to_integer(unsigned(endpoint_config(route_entry).size)));
    in_buffer <= '1' when (byte_count < buffer_bytes) else '0';

    routed.valid <= rx.valid and route_accept;
    routed.data <= rx.data;
    routed.keep <= rx.keep when (in_buffer = '1') else (others => '0');
    routed.dest <= route(DEST_WIDTH downto 0);
    routed.last <= rx.last;
    rx.ready <= routed.ready when (route_accept = '1') else '1';
    rx_fire <= rx.valid and rx.ready;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            first <= '1';
            route_held <= (others => '0');
            byte_count_held <= (others => '0');
        elsif rising_edge(clk) then
            if (rx_fire = '1') then
                first <= rx.last;
                route_held <= route;
                if (rx.keep(0) = '1' and in_buffer = '1') then
                    byte_count_held <= byte_count + 1;
                else
                    byte_count_held <= byte_count;
                end if;
            end if;
        end if;
    end process;

    output_buffer_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH + 1
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => routed.data,
            ckeep  => routed.keep,
            cdest  => routed.dest,
            clast  => routed.last,
            cvalid => routed.valid,
            cready => routed.ready,
            odata  => routed_tdata,
            okeep  => routed_tkeep,
            odest  => routed_tdest,
            olast  => routed_tlast,
            ovalid => routed_tvalid,
            oready => routed_tready
        );

end architecture rtl;
