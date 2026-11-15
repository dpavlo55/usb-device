library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_packet_parser is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk            : in  std_ulogic;
        rst_n          : in  std_ulogic;

        packet_tdata   : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        packet_tkeep   : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        packet_tdest   : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        packet_tlast   : in  std_ulogic;
        packet_tvalid  : in  std_ulogic;
        packet_tready  : out std_ulogic;

        payload_tdata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        payload_tkeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        payload_tdest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        payload_tlast  : out std_ulogic;
        payload_tvalid : out std_ulogic;
        payload_tready : in  std_ulogic;

        packet         : out packet_t
    );
end entity;

architecture rtl of usb_packet_parser is

    constant PAYLOAD_BUFFER_DEPTH : natural := 2;
    constant PAYLOAD_BYTE_OFFSET : natural := 3;

    type state_t is (
        IDLE,
        PID,
        TOKEN_1,
        TOKEN_2,
        DATA,
        CRC5,
        CRC16
    );

    type buffer_t is array (natural range <>) of std_ulogic_vector;

    signal state : state_t;

    signal packet_is_token : std_ulogic;
    signal packet_is_data : std_ulogic;
    signal packet_is_handshake : std_ulogic;
    signal packet_is_special : std_ulogic;

    signal crc5_next : std_ulogic_vector(4 downto 0);
    signal crc5_tdata : std_ulogic_vector(10 downto 0);
    signal crc5_tkeep : std_ulogic_vector(1 downto 0);
    signal crc5_tvalid : std_ulogic;
    signal crc5_tlast : std_ulogic;
    signal crc5_out : std_ulogic_vector(4 downto 0);
    signal crc5_valid : std_ulogic;

    signal crc16_next : std_ulogic_vector(15 downto 0);
    signal crc16_tdata : std_ulogic_vector(7 downto 0);
    signal crc16_tkeep : std_ulogic_vector(0 downto 0);
    signal crc16_tvalid : std_ulogic;
    signal crc16_tlast : std_ulogic;
    signal crc16_out : std_ulogic_vector(15 downto 0);
    signal crc16_valid : std_ulogic;

    signal fifo : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));
    signal ipacket : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));
    signal payload : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal ipacket_fire : std_ulogic;
    signal payload_fire : std_ulogic;
    signal byte_counter : unsigned(10 downto 0);
    signal payload_buffer : buffer_t(0 to PAYLOAD_BUFFER_DEPTH - 1)(
        payload.data'range);

begin

    elastic_fifo_inst : entity work.elastic_fifo
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => packet_tdata,
            ikeep  => packet_tkeep,
            idest  => packet_tdest,
            ilast  => packet_tlast,
            ivalid => packet_tvalid,
            iready => packet_tready,
            odata  => fifo.data,
            okeep  => fifo.keep,
            odest  => fifo.dest,
            olast  => fifo.last,
            ovalid => fifo.valid,
            oready => fifo.ready
        );

    input_buffer_inst : entity work.input_buffer
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => fifo.data,
            ikeep  => fifo.keep,
            idest  => fifo.dest,
            ilast  => fifo.last,
            ivalid => fifo.valid,
            iready => fifo.ready,
            cdata  => ipacket.data,
            ckeep  => ipacket.keep,
            cdest  => ipacket.dest,
            clast  => ipacket.last,
            cvalid => ipacket.valid,
            cready => ipacket.ready
        );

    ipacket_fire <= ipacket.valid and ipacket.ready;
    payload_fire <= payload.valid and payload.ready;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            byte_counter <= (others => '0');
        elsif rising_edge(clk) then
            if (ipacket_fire = '1') then
                if (ipacket.last = '1') then
                    byte_counter <= (others => '0');
                elsif (ipacket.keep(0) = '1') then
                    byte_counter <= byte_counter + 1;
                end if;
            end if;
        end if;
    end process;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            payload_buffer <= (others => (others => '0'));
        elsif rising_edge(clk) then
            if (ipacket_fire = '1' and ipacket.keep(0) = '1' and packet_is_data = '1') then
                payload_buffer(0) <= ipacket.data;
                for i in 1 to payload_buffer'length - 1 loop
                    payload_buffer(i) <= payload_buffer(i - 1);
                end loop;
            end if;
        end if;
    end process;

    ipacket.ready <= (byte_counter ?<= PAYLOAD_BYTE_OFFSET) or payload.ready;

    payload.data <= payload_buffer(payload_buffer'high);
    payload.keep <= ipacket.keep;
    payload.dest <= ipacket.dest;
    payload.last <= ipacket.last;
    payload.valid <= ipacket.valid and (byte_counter ?>= PAYLOAD_BYTE_OFFSET);

    output_buffer_inst : entity work.output_buffer
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => payload.data,
            ckeep  => payload.keep,
            cdest  => payload.dest,
            clast  => payload.last,
            cvalid => payload.valid,
            cready => payload.ready,
            odata  => payload_tdata,
            okeep  => payload_tkeep,
            odest  => payload_tdest,
            olast  => payload_tlast,
            ovalid => payload_tvalid,
            oready => payload_tready
        );

    packet_is_token <= '1' when
        (packet.pid = PID_SETUP) or
        (packet.pid = PID_IN) or
        (packet.pid = PID_OUT) or
        (packet.pid = PID_SOF)
        else '0';

    packet_is_data <= '1' when
        (packet.pid = PID_DATA0) or
        (packet.pid = PID_DATA1)
        else '0';

    packet_is_handshake <= '1' when
        (packet.pid = PID_ACK) or
        (packet.pid = PID_NAK) or
        (packet.pid = PID_STALL)
        else '0';

    packet_is_special <= '1' when
        (packet.pid = PID_PRE)
        else '0';

    process (clk, rst_n)
        variable pid_next : pid_t;
    begin
        if (rst_n = '0') then
            packet <= PACKET_INIT;
            state <= IDLE;
        elsif rising_edge(clk) then
            packet.valid <= '0';
            case state is
                ----------------------------------------------------------------
                when IDLE =>
                    packet <= PACKET_INIT;
                    if (ipacket_fire = '1') then
                        packet.pid_ok <= check_pid(fifo.data);
                        pid_next := to_pid(fifo.data(3 downto 0));
                        packet.pid <= pid_next;
                        packet.packet_type <= to_packet_type(pid_next);
                        state <= PID;
                    end if;
                ----------------------------------------------------------------
                when PID =>
                    if (ipacket_fire = '1') then
                        if (packet_is_token = '1') then
                            crc5_tdata(7 downto 0) <= fifo.data;
                            state <= TOKEN_1;
                            if (packet.pid /= PID_SOF) then
                                packet.address <= fifo.data(6 downto 0);
                                packet.endpoint(0) <= fifo.data(7);
                            else
                                packet.frame_number(7 downto 0) <= fifo.data;
                            end if;
                        elsif (packet_is_data = '1') then
                            state <= DATA;
                        elsif (packet_is_handshake = '1') then
                            packet.valid <= '1';
                            state <= IDLE;
                        elsif (packet_is_special = '1') then
                            packet.valid <= '1';
                            state <= IDLE;
                        else
                            state <= IDLE;
                        end if;
                    end if;
                ----------------------------------------------------------------
                when TOKEN_1 =>
                    if (ipacket_fire = '1') then
                        if (packet.pid /= PID_SOF) then
                            packet.endpoint(3 downto 1) <= fifo.data(2 downto 0);
                        else
                            packet.frame_number(10 downto 8) <= fifo.data(2 downto 0);
                        end if;
                        crc5_next <= fifo.data(7 downto 3);
                        crc5_tdata(10 downto 8) <= fifo.data(2 downto 0);
                        state <= TOKEN_2;
                    end if;
                ----------------------------------------------------------------
                when TOKEN_2 =>
                    if (ipacket_fire = '1') then
                        if (packet_is_token = '1') then
                            if (packet.pid /= PID_SOF) then
                                packet.frame_number <= (others => '0');
                            else
                                packet.address <= (others => '0');
                                packet.endpoint <= (others => '0');
                            end if;
                        end if;
                        state <= CRC5;
                    end if;
                ----------------------------------------------------------------
                when DATA =>
                    if (ipacket_fire = '1') then
                        if (ipacket.last = '1') then
                            packet.payload_length <= std_ulogic_vector(
                                (byte_counter(9 downto 0) - PAYLOAD_BYTE_OFFSET));
                            state <= CRC16;
                        end if;
                    end if;
                ----------------------------------------------------------------
                when CRC5 =>
                    if (crc5_valid = '1') then
                        packet.crc_ok <= check_crc(crc5_next, crc5_out);
                        packet.valid <= '1';
                        state <= IDLE;
                    end if;
                ----------------------------------------------------------------
                when CRC16 =>
                    if (crc16_valid = '1') then
                        packet.crc_ok <= check_crc(crc16_next, crc16_out);
                        packet.valid <= '1';
                        state <= IDLE;
                    end if;
                ----------------------------------------------------------------
            end case;
        end if;
    end process;

    crc5_tkeep <= (others => '1');
    crc5_tvalid <= ipacket_fire and fifo.last;
    crc5_tlast <= ipacket_fire and fifo.last;

    usb_crc5_inst : entity work.crc
        generic map (
            DATA_WIDTH => 11,
            CRC_WIDTH  => 5,
            POLY       => "00101" -- x^5 + x^2 + 1
        )
        port map (
            clk        => clk,
            rst_n      => rst_n,
            tdata      => crc5_tdata,
            tkeep      => crc5_tkeep,
            tvalid     => crc5_tvalid,
            tlast      => crc5_tlast,
            crc_out    => crc5_out,
            crc_valid  => crc5_valid
        );

    crc16_next <= payload_buffer(0) & payload_buffer(1);

    crc16_tdata <= payload.data;
    crc16_tkeep <= payload.keep;
    crc16_tvalid <= payload_fire and packet_is_data;
    crc16_tlast <= payload_fire and payload.last;

    usb_crc16_inst : entity work.crc
        generic map (
            DATA_WIDTH => 8,
            CRC_WIDTH  => 16,
            POLY       => "1000000000000101" -- x^16 + x^15 + x^2 + 1
        )
        port map (
            clk        => clk,
            rst_n      => rst_n,
            tdata      => crc16_tdata,
            tkeep      => crc16_tkeep,
            tvalid     => crc16_tvalid,
            tlast      => crc16_tlast,
            crc_out    => crc16_out,
            crc_valid  => crc16_valid
        );

end architecture;