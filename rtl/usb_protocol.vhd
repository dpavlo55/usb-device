library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;

entity usb_protocol is
    generic (
        ADDR_WIDTH : natural := 10;
        DEST_WIDTH : natural := 4
    );
    port (
        clk             : in  std_ulogic;
        rst_n           : in  std_ulogic;

        rx_packet       : in  packet_t;
        reset_detected  : in  std_ulogic;

        enable_core     : in  std_ulogic;
        device_address  : in  std_ulogic_vector(6 downto 0);
        endpoint_config : in  endpoint_config_array_t(0 to 2 ** DEST_WIDTH - 1)(
            base_address(ADDR_WIDTH - 1 downto 0));
        armed           : in  std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        reset_toggle    : in  std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        in_busy         : in  std_ulogic;
        in_last         : in  std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);

        rx_route        : out rx_route_t;
        tx_packet       : out packet_t;
        setup_received  : out std_ulogic;
        out_result      : out out_result_t;
        in_request      : out packet_descriptor_t;
        in_result       : out in_result_t
    );
end entity usb_protocol;

architecture rtl of usb_protocol is

    subtype entry_t is natural range 0 to 2 ** DEST_WIDTH - 1;

    signal token_is_out : std_ulogic;
    signal token_is_setup : std_ulogic;
    signal token_is_in : std_ulogic;
    signal token_direction : std_ulogic;
    signal token_for_device : std_ulogic;
    signal paired : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal partner : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal next_buffer : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal entry_match : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal match_found : std_ulogic;
    signal match_entry : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal match_index : entry_t;
    signal token_matched : std_ulogic;
    signal serve_index : entry_t;
    signal serve_entry : std_ulogic_vector(DEST_WIDTH - 1 downto 0);

    signal packet_is_data : std_ulogic;
    signal data_ok : std_ulogic;
    signal data_pid_bit : std_ulogic;
    signal setup_data_ok : std_ulogic;
    signal setup_accepted : std_ulogic;

    signal out_pending : std_ulogic;
    signal out_entry : entry_t;
    signal out_leader : entry_t;
    signal out_paired : std_ulogic;
    signal out_stall : std_ulogic;
    signal out_armed : std_ulogic;
    signal out_isochronous : std_ulogic;
    signal out_size : std_ulogic_vector(2 downto 0);
    signal data_too_long : std_ulogic;

    signal in_pending : std_ulogic;
    signal in_entry : entry_t;
    signal in_leader : entry_t;
    signal in_paired : std_ulogic;
    signal ack_received : std_ulogic;

    signal toggle : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);

begin

    token_is_out <= '1' when (rx_packet.pid = PID_OUT) else '0';
    token_is_setup <= '1' when (rx_packet.pid = PID_SETUP) else '0';
    token_is_in <= '1' when (rx_packet.pid = PID_IN) else '0';
    token_direction <= ENDPOINT_DIRECTION_IN when (token_is_in = '1') else ENDPOINT_DIRECTION_OUT;
    token_for_device <= '1' when (
        enable_core = '1' and
        rx_packet.pid_ok = '1' and
        rx_packet.crc_ok = '1' and
        rx_packet.address = device_address
    ) else '0';

    paired_gen : for i in paired'range generate
        paired(i) <= '1' when (
            i < paired'high and
            endpoint_config(i).double_buffer = '1' and
            endpoint_config(i).kind /= ENDPOINT_KIND_CONTROL
        ) else '0';
    end generate;

    partner <= paired(paired'high - 1 downto 0) & '0';

    entry_gen : for i in entry_match'range generate
        entry_match(i) <= '1' when (
            partner(i) = '0' and
            endpoint_config(i).enable = '1' and
            endpoint_config(i).direction = token_direction and
            endpoint_config(i).endpoint_number = rx_packet.endpoint and
            (token_is_out = '1' or token_is_in = '1' or
             (token_is_setup = '1' and endpoint_config(i).kind = ENDPOINT_KIND_CONTROL))
        ) else '0';
    end generate;

    -- the lowest matching entry wins
    process (all)
    begin
        match_found <= '0';
        match_entry <= (others => '0');
        for i in entry_match'high downto 0 loop
            if (entry_match(i) = '1') then
                match_found <= '1';
                match_entry <= std_ulogic_vector(to_unsigned(i, DEST_WIDTH));
            end if;
        end loop;
    end process;

    match_index <= to_integer(unsigned(match_entry));
    token_matched <= rx_packet.valid and token_for_device and match_found;
    serve_index <= match_index + 1 when (paired(match_index) = '1' and next_buffer(match_index) = '1') else
        match_index;
    serve_entry <= std_ulogic_vector(to_unsigned(serve_index, DEST_WIDTH));

    packet_is_data <= '1' when (rx_packet.pid = PID_DATA0 or rx_packet.pid = PID_DATA1) else '0';
    data_ok <= rx_packet.valid and packet_is_data and rx_packet.pid_ok and rx_packet.crc_ok;
    data_pid_bit <= '1' when (rx_packet.pid = PID_DATA1) else '0';

    setup_data_ok <= '1' when (
        rx_packet.pid = PID_DATA0 and
        unsigned(rx_packet.payload_length) = 8
    ) else '0';

    setup_accepted <= data_ok and rx_route.accept and rx_route.setup and setup_data_ok;

    -- More data than the entry's buffer holds (2 ** (size + 3) bytes) is a packet
    -- error: no handshake, nothing reported, the toggle stays.
    data_too_long <= '1' when resize(unsigned(rx_packet.payload_length), 11) >
        shift_left(to_unsigned(8, 11), to_integer(unsigned(out_size))) else '0';

    ack_received <= '1' when (in_pending = '1' and rx_packet.pid = PID_ACK and rx_packet.pid_ok = '1') else '0';

    process (clk, rst_n)
        procedure send(pid : pid_t) is
        begin
            tx_packet.pid <= pid;
            tx_packet.packet_type <= HANDSHAKE;
            tx_packet.pid_ok <= '1';
            tx_packet.valid <= '1';
        end procedure;

        procedure send_data(data_pid : std_ulogic) is
        begin
            tx_packet.pid <= PID_DATA1 when (data_pid = '1') else PID_DATA0;
            tx_packet.packet_type <= DATA;
            tx_packet.pid_ok <= '1';
            tx_packet.valid <= '1';
        end procedure;
    begin
        if (rst_n = '0') then
            rx_route <= RX_ROUTE_INIT;
            tx_packet <= PACKET_INIT;
            setup_received <= '0';
            out_result <= OUT_RESULT_INIT;
            out_pending <= '0';
            out_entry <= 0;
            out_leader <= 0;
            out_paired <= '0';
            out_stall <= '0';
            out_armed <= '0';
            out_isochronous <= '0';
            out_size <= (others => '0');
            in_request <= PACKET_DESCRIPTOR_INIT;
            in_result <= IN_RESULT_INIT;
            in_pending <= '0';
            in_entry <= 0;
            in_leader <= 0;
            in_paired <= '0';
            toggle <= (others => '0');
            next_buffer <= (others => '0');
        elsif rising_edge(clk) then
            tx_packet.valid <= '0';
            setup_received <= '0';
            out_result.complete <= '0';
            out_result.overrun <= '0';
            in_request.start <= '0';
            in_result.complete <= '0';

            if (reset_detected = '1') then
                rx_route.accept <= '0';
                out_pending <= '0';
                in_pending <= '0';
                toggle <= (others => '0');
                next_buffer <= (others => '0');
            elsif (rx_packet.valid = '1') then
                in_pending <= '0';
                if (ack_received = '1') then
                    in_result.entry <= std_ulogic_vector(to_unsigned(in_entry, in_result.entry'length));
                    in_result.complete <= '1';
                    toggle(in_leader) <= not toggle(in_leader);
                    if (in_paired = '1') then
                        next_buffer(in_leader) <= not next_buffer(in_leader);
                    end if;
                end if;

                if (setup_accepted = '1') then
                    send(PID_ACK);
                    setup_received <= '1';
                    for i in toggle'range loop
                        if (endpoint_config(i).endpoint_number =
                            endpoint_config(to_integer(unsigned(rx_route.entry))).endpoint_number) then
                            toggle(i) <= '1';
                        end if;
                    end loop;
                elsif (data_ok = '1' and out_pending = '1' and data_too_long = '0') then
                    out_result.entry <= std_ulogic_vector(to_unsigned(out_entry, out_result.entry'length));
                    out_result.byte_count <= rx_packet.payload_length;
                    if (out_stall = '1') then
                        if (out_isochronous = '0') then
                            send(PID_STALL);
                        end if;
                    elsif (out_armed = '0') then
                        if (out_isochronous = '0') then
                            send(PID_NAK);
                        end if;
                        out_result.overrun <= '1';
                    elsif (out_isochronous = '1') then
                        out_result.complete <= '1';
                        if (out_paired = '1') then
                            next_buffer(out_leader) <= not next_buffer(out_leader);
                        end if;
                    else
                        send(PID_ACK);
                        if (data_pid_bit = toggle(out_leader)) then
                            out_result.complete <= '1';
                            toggle(out_leader) <= not toggle(out_leader);
                            if (out_paired = '1') then
                                next_buffer(out_leader) <= not next_buffer(out_leader);
                            end if;
                        end if;
                    end if;
                end if;

                rx_route.accept <= '0';
                out_pending <= '0';
                if (token_matched = '1' and token_is_in = '1') then
                    if (endpoint_config(match_index).kind = ENDPOINT_KIND_ISOCHRONOUS) then
                        if (endpoint_config(match_index).stall = '0' and armed(serve_index) = '1' and
                            in_busy = '0') then
                            send_data('0');
                            in_request.endpoint_number <= serve_entry;
                            in_request.start <= '1';
                            if (paired(match_index) = '1' and in_last(serve_index) = '1') then
                                next_buffer(match_index) <= not next_buffer(match_index);
                            end if;
                        end if;
                    elsif (endpoint_config(match_index).stall = '1') then
                        send(PID_STALL);
                    elsif (armed(serve_index) = '0' or in_busy = '1') then
                        send(PID_NAK);
                    else
                        send_data(toggle(match_index));
                        in_request.endpoint_number <= serve_entry;
                        in_request.start <= '1';
                        in_pending <= '1';
                        in_entry <= serve_index;
                        in_leader <= match_index;
                        in_paired <= paired(match_index) and in_last(serve_index);
                    end if;
                elsif (token_matched = '1') then
                    rx_route.entry <= serve_entry;
                    rx_route.setup <= token_is_setup;
                    if (token_is_setup = '1') then
                        rx_route.accept <= '1';
                    else
                        rx_route.accept <= armed(serve_index) and not endpoint_config(match_index).stall;
                        out_pending <= '1';
                        out_entry <= serve_index;
                        out_leader <= match_index;
                        out_paired <= paired(match_index);
                        out_stall <= endpoint_config(match_index).stall;
                        out_armed <= armed(serve_index);
                        out_isochronous <= '1' when
                            (endpoint_config(match_index).kind = ENDPOINT_KIND_ISOCHRONOUS) else '0';
                        out_size <= endpoint_config(serve_index).size;
                    end if;
                end if;
            end if;

            for i in next_buffer'range loop
                if (paired(i) = '0' or endpoint_config(i).enable = '0') then
                    next_buffer(i) <= '0';
                end if;
            end loop;

            for i in reset_toggle'range loop
                if (reset_toggle(i) = '1') then
                    toggle(i) <= '0';
                end if;
            end loop;
        end if;
    end process;

end architecture rtl;
