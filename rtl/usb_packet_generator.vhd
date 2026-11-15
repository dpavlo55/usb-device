library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_packet_generator is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk            : in  std_ulogic;
        rst_n          : in  std_ulogic;

        packet         : in  packet_t;

        payload_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        payload_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        payload_tdest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        payload_tlast  : in  std_ulogic;
        payload_tvalid : in  std_ulogic;
        payload_tready : out std_ulogic;

        packet_tdata   : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        packet_tkeep   : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        packet_tdest   : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        packet_tlast   : out std_ulogic;
        packet_tvalid  : out std_ulogic;
        packet_tready  : in  std_ulogic
    );
end usb_packet_generator;

architecture rtl of usb_packet_generator is

    type fifo_sel_t is (
        CONTROL_STREAM,
        PAYLOAD_STREAM
    );

    type state_t is (
        IDLE,
        SYNC,
        PID,
        DATA,
        CRC_1,
        CRC_2,
        EOP
    );

    signal state : state_t;

    signal packet_is_supported : std_ulogic;
    signal packet_is_handshake : std_ulogic;
    signal packet_is_data : std_ulogic;

    signal payload : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));
    signal fifo : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));
    signal control : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal fifo_sel : fifo_sel_t;

    signal control_fire : std_ulogic;
    signal payload_fire : std_ulogic;

    signal crc16_tdata : std_ulogic_vector(DATA_WIDTH - 1 downto 0);
    signal crc16_tkeep : std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
    signal crc16_tvalid : std_ulogic;
    signal crc16_tlast : std_ulogic;
    signal crc16_out : std_ulogic_vector(15 downto 0);
    signal crc16_valid : std_ulogic;
    signal crc16_last : std_ulogic;

begin

    elastic_fifo_inst : entity work.elastic_fifo
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => fifo.data,
            ikeep  => fifo.keep,
            idest  => fifo.dest,
            ilast  => fifo.last,
            ivalid => fifo.valid,
            iready => fifo.ready,
            odata  => packet_tdata,
            okeep  => packet_tkeep,
            odest  => packet_tdest,
            olast  => packet_tlast,
            ovalid => packet_tvalid,
            oready => packet_tready
        );

    packet_is_handshake <= '1' when (packet.packet_type = HANDSHAKE) else '0';
    packet_is_data <= '1' when (packet.packet_type = DATA) else '0';
    packet_is_supported <= packet_is_handshake or packet_is_data;

    -- FIFO mux
    fifo.data <= control.data when (fifo_sel = CONTROL_STREAM) else payload_tdata;
    fifo.keep <= control.keep when (fifo_sel = CONTROL_STREAM) else payload_tkeep;
    fifo.dest <= control.dest when (fifo_sel = CONTROL_STREAM) else payload_tdest;
    fifo.last <= control.last when (fifo_sel = CONTROL_STREAM) else '0';
    fifo.valid <=
        control.valid when (fifo_sel = CONTROL_STREAM) else
        payload_tvalid when (payload_tkeep(0) = '1') else
        '0';
    control.ready <= fifo.ready when (fifo_sel = CONTROL_STREAM) else '0';
    payload_tready <= fifo.ready when (fifo_sel = PAYLOAD_STREAM) else '0';

    control_fire <= control.valid and control.ready;
    payload_fire <= payload_tvalid and payload_tready;


    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            state <= IDLE;
            fifo_sel <= CONTROL_STREAM;
            control.data <= (others => '0');
            control.keep <= (others => '0');
            control.dest <= (others => '0');
            control.last <= '0';
            control.valid <= '0';
        elsif rising_edge(clk) then
            case state is
                ----------------------------------------------------------------
                when IDLE =>
                    if (packet.valid = '1' and packet_is_supported = '1') then
                        control.data <= SYNC_BYTE;
                        control.keep <= (others => '1');
                        control.valid <= '1';
                        state <= SYNC;
                    end if;
                ----------------------------------------------------------------
                when SYNC =>
                    if (control_fire = '1') then
                        control.data <= to_pid_byte(packet.pid);
                        control.keep <= (others => '1');
                        control.valid <= '1';
                        state <= PID;
                        if (packet_is_handshake = '1') then
                            control.last <= '1';
                        elsif (packet_is_data = '1') then
                            control.last <= '0';
                        end if;
                    end if;
                ----------------------------------------------------------------
                when PID =>
                    if (control_fire = '1') then
                        control.data <= (others => '0');
                        control.keep <= (others => '0');
                        control.dest <= (others => '0');
                        control.last <= '0';
                        control.valid <= '0';
                        if (packet_is_handshake = '1') then
                            state <= IDLE;
                        elsif (packet_is_data = '1') then
                            fifo_sel <= PAYLOAD_STREAM;
                            state <= DATA;
                        end if;
                    end if;
                ----------------------------------------------------------------
                when DATA =>
                    if (payload_fire = '1' and payload_tlast = '1') then
                        fifo_sel <= CONTROL_STREAM;
                        state <= CRC_1;
                    end if;
                ----------------------------------------------------------------
                when CRC_1 =>
                    if (crc16_valid = '1') then
                        control.data <= crc16_out(7 downto 0);
                        control.keep <= (others => '1');
                        control.dest <= (others => '0');
                        control.last <= '0';
                        control.valid <= '1';
                        state <= CRC_2;
                    end if;
                ----------------------------------------------------------------
                when CRC_2 =>
                    if (control_fire = '1') then
                        control.data <= crc16_out(15 downto 8);
                        control.keep <= (others => '1');
                        control.dest <= (others => '0');
                        control.last <= '1';
                        control.valid <= '1';
                        state <= EOP;
                    end if;
                ----------------------------------------------------------------
                when EOP =>
                    if (control_fire = '1') then
                        control.data <= (others => '0');
                        control.keep <= (others => '0');
                        control.dest <= (others => '0');
                        control.last <= '0';
                        control.valid <= '0';
                        state <= IDLE;
                    end if;
                ----------------------------------------------------------------
            end case;
        end if;
    end process;

    crc16_tdata <= payload_tdata;
    crc16_tkeep <= payload_tkeep;
    crc16_tvalid <= payload_fire;
    crc16_tlast <= payload_tlast;

    usb_crc16_inst : entity work.crc
        generic map (
            DATA_WIDTH => 8,
            CRC_WIDTH  => 16,
            POLY       => "1000000000000101" -- x^16 + x^15 + x^2 + 1
        )
        port map (
            clk       => clk,
            rst_n     => rst_n,
            tdata     => crc16_tdata,
            tkeep     => crc16_tkeep,
            tlast     => crc16_tlast,
            tvalid    => crc16_tvalid,
            crc_out   => crc16_out,
            crc_valid => crc16_valid
        );

end architecture rtl;