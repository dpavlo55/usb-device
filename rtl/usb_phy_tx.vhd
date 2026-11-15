library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_phy_tx is
    generic (
        DATA_WIDTH : natural := 1;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 1
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        serial_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        serial_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        serial_tdest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        serial_tlast  : in  std_ulogic;
        serial_tvalid : in  std_ulogic;
        serial_tready : out std_ulogic;

        usb_oe : out std_ulogic;
        usb_dp : out std_ulogic;
        usb_dn : out std_ulogic
    );
end entity usb_phy_tx;

architecture rtl of usb_phy_tx is

    constant USB_BIT_CLOCK_CYCLES : natural := 4 - 1;
    constant EOP_SE0_BITS : natural := 2;

    type state_t is (
        IDLE,
        STREAM,
        EOP
    );

    signal state : state_t;

    signal serial : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal stuff : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal nrzi : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal usb : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal serial_fire : std_ulogic;
    signal stuff_fire : std_ulogic;
    signal nrzi_fire : std_ulogic;

    signal ones_cnt : unsigned(2 downto 0);
    signal shift_reg : std_ulogic_vector(5 downto 0);

    signal stuff_en : std_ulogic;
    signal nrzi_state : std_ulogic;

    signal sixth_one : std_ulogic;
    signal stuff_pending : std_ulogic;

    signal bit_cnt : unsigned(1 downto 0);
    signal bit_tick : std_ulogic;
    signal eop_cnt : unsigned(1 downto 0);

    signal usb_line_state_next : usb_line_state_t;
    signal usb_line_state : usb_line_state_t;
    signal usb_line_state_valid : std_ulogic;
    signal usb_oe_next : std_ulogic;

begin

    assert DATA_WIDTH = 1
        report "usb_phy_tx requires DATA_WIDTH = 1"
        severity failure;

    assert KEEP_WIDTH = 1
        report "usb_phy_tx requires KEEP_WIDTH = 1"
        severity failure;

    input_buffer_inst : entity work.input_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => serial_tdata,
            ikeep  => serial_tkeep,
            idest  => serial_tdest,
            ilast  => serial_tlast,
            ivalid => serial_tvalid,
            iready => serial_tready,
            cdata  => serial.data,
            ckeep  => serial.keep,
            cdest  => serial.dest,
            clast  => serial.last,
            cvalid => serial.valid,
            cready => serial.ready
        );

    serial_fire <= serial.valid and serial.ready;
    stuff_fire <= stuff.valid and stuff.ready;

    serial.ready <= stuff.ready and (not stuff_en);
    stuff.valid <= serial.valid or stuff_en;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            shift_reg <= (others => '0');
        elsif rising_edge(clk) then
            if (stuff_fire = '1') then
                if (stuff.last = '1') then
                    shift_reg <= (others => '0');
                else
                    shift_reg(5) <= stuff.data(0);
                    shift_reg(4 downto 0) <= shift_reg(5 downto 1);
                end if;
            end if;
        end if;
    end process;

    sixth_one <= '1' when (
        serial.data(0) = '1' and shift_reg(5 downto 1) = "11111") else '0';

    stuff_en <= '1' when (shift_reg = "111111") else '0';

    stuff.data <= serial.data when (stuff_en = '0') else (others => '0');
    stuff.keep <= serial.keep when (stuff_en = '0') else (others => '1');
    stuff.dest <= serial.dest when (stuff_en = '0') else (others => '0');
    stuff.last <=
        (serial.last and (not sixth_one)) when (stuff_en = '0')
        else stuff_pending;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            stuff_pending <= '0';
        elsif rising_edge(clk) then
            if (stuff_fire = '1') then
                if (stuff_en = '1') then
                    stuff_pending <= '0';
                else
                    stuff_pending <= serial.last and sixth_one;
                end if;
            end if;
        end if;
    end process;

    nrzi_fire <= stuff.valid and nrzi.ready;
    stuff.ready <= nrzi.ready;
    nrzi.valid <= stuff.valid;

    nrzi.data <= (not stuff.data) when (nrzi_state = '0') else stuff.data;
    nrzi.keep <= stuff.keep;
    nrzi.dest <= stuff.dest;
    nrzi.last <= stuff.last;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            nrzi_state <= '1';
        elsif rising_edge(clk) then
            if (nrzi_fire = '1') then
                if (stuff.last = '1') then
                    nrzi_state <= '1';
                elsif (stuff.data(0) = '0') then
                    nrzi_state <= not nrzi_state;
                end if;
            end if;
        end if;
    end process;

    output_buffer_nrzi_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => nrzi.data,
            ckeep  => nrzi.keep,
            cdest  => nrzi.dest,
            clast  => nrzi.last,
            cvalid => nrzi.valid,
            cready => nrzi.ready,
            odata  => usb.data,
            okeep  => usb.keep,
            odest  => usb.dest,
            olast  => usb.last,
            ovalid => usb.valid,
            oready => usb.ready
        );

    usb_line_state_next <= to_usb_line_state(usb.data(0), not usb.data(0));

    bit_tick <= '1' when (bit_cnt = USB_BIT_CLOCK_CYCLES) else '0';

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            state <= IDLE;
            usb.ready <= '0';
            bit_cnt <= (others => '0');
            eop_cnt <= (others => '0');
            usb_line_state <= USB_FS_J;
            usb_line_state_valid <= '0';
            usb_oe_next <= '0';
        elsif rising_edge(clk) then
            usb.ready <= '0';
            usb_line_state_valid <= '0';
            case (state) is
                ----------------------------------------------------------------
                when IDLE =>
                    if (usb.valid = '1') then
                        state <= STREAM;
                    end if;
                ----------------------------------------------------------------
                when STREAM =>
                    bit_cnt <= bit_cnt + 1;
                    if (usb.valid = '1' and bit_tick = '1') then
                        usb.ready <= '1';
                        usb_line_state <= usb_line_state_next;
                        usb_line_state_valid <= '1';
                        usb_oe_next <= '1';
                        if (usb.last = '1') then
                            state <= EOP;
                        end if;
                    end if;
                ----------------------------------------------------------------
                when EOP =>
                    bit_cnt <= bit_cnt + 1;
                    if (bit_tick = '1') then
                        usb_line_state_valid <= '1';
                        if (eop_cnt < EOP_SE0_BITS) then
                            usb_line_state <= USB_FS_SE0;
                        else
                            usb_line_state <= USB_FS_J;
                        end if;
                        eop_cnt <= eop_cnt + 1;
                        if (eop_cnt = 3) then
                            usb_oe_next <= '0';
                            bit_cnt <= (others => '0');
                            eop_cnt <= (others => '0');
                            state <= IDLE;
                        end if;
                    end if;
                ----------------------------------------------------------------
            end case;
        end if;
    end process;

    -- output USB driving process
    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            usb_oe <= '0';
            usb_dp <= '0';
            usb_dn <= '0';
        elsif rising_edge(clk) then
            if (usb_line_state_valid = '1') then
                usb_oe <= usb_oe_next;
                usb_dp <= to_usb_dp(usb_line_state);
                usb_dn <= to_usb_dn(usb_line_state);
            end if;
        end if;
    end process;

end architecture rtl;
