library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;

entity usb_endpoints_controller is
    generic (
        ADDR_WIDTH : natural := 10;
        DEST_WIDTH : natural := 4
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        enable_core     : in std_ulogic;
        endpoint_config : in endpoint_config_array_t(0 to 2 ** DEST_WIDTH - 1)(
            base_address(ADDR_WIDTH - 1 downto 0));
        transfer_length : in vector_array_t(0 to 2 ** DEST_WIDTH - 1)(9 downto 0);
        arm             : in std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        descriptor      : in packet_descriptor_t;
        setup_writing   : in std_ulogic;
        setup_entry     : in std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        setup_written   : in std_ulogic;
        out_result      : in out_result_t;
        in_result       : in in_result_t;

        endpoint_base_address : out vector_array_t(0 to 2 ** DEST_WIDTH - 1)(ADDR_WIDTH - 1 downto 0);
        write_allowed         : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        read_command          : out ram_read_command_t(base_address(ADDR_WIDTH - 1 downto 0));
        in_report             : in  packet_report_t;
        armed                 : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        in_last               : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        in_busy               : out std_ulogic;

        event       : out transfer_event_t;
        event_ready : in  std_ulogic
    );
end entity usb_endpoints_controller;

architecture rtl of usb_endpoints_controller is

    subtype endpoint_t is natural range 0 to 2 ** DEST_WIDTH - 1;

    signal out_en : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal in_en : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal armed_length : vector_array_t(0 to 2 ** DEST_WIDTH - 1)(9 downto 0);
    signal sent : vector_array_t(0 to 2 ** DEST_WIDTH - 1)(9 downto 0);
    signal remaining : vector_array_t(0 to 2 ** DEST_WIDTH - 1)(10 downto 0);
    signal packet_bytes : vector_array_t(0 to 2 ** DEST_WIDTH - 1)(10 downto 0);
    signal in_last_entry : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
    signal in_done : std_ulogic;

    signal request_endpoint : endpoint_t;
    signal read_start : std_ulogic;
    signal reading : std_ulogic;

    signal out_endpoint : endpoint_t;
    signal in_endpoint : endpoint_t;
    signal read_endpoint : endpoint_t;
    signal read_done_isochronous : std_ulogic;
    signal in_complete : std_ulogic;
    signal out_complete : std_ulogic;
    signal out_overrun : std_ulogic;
    signal setup_endpoint : endpoint_t;
    signal setup_write : std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);

begin

    endpoint_gen : for i in 0 to 2 ** DEST_WIDTH - 1 generate
        out_en(i) <= enable_core and endpoint_config(i).enable when
            (endpoint_config(i).direction = ENDPOINT_DIRECTION_OUT) else '0';
        in_en(i) <= enable_core and endpoint_config(i).enable when
            (endpoint_config(i).direction = ENDPOINT_DIRECTION_IN) else '0';
        endpoint_base_address(i) <= endpoint_config(i).base_address;
        setup_write(i) <= setup_writing when (setup_endpoint = i) else '0';
        transfer_split_gen : block
            signal buffer_bytes : unsigned(10 downto 0);
            signal total : unsigned(10 downto 0);
        begin
            -- An IN transfer is sent as packets of 2 ** (size + 3 - split) bytes.
            buffer_bytes <= shift_left(to_unsigned(8, 11), to_integer(unsigned(endpoint_config(i).size)));
            packet_bytes(i) <= std_ulogic_vector(shift_right(buffer_bytes, to_integer(unsigned(endpoint_config(i).split))))
                when (unsigned(endpoint_config(i).split) <= unsigned(endpoint_config(i).size)) else
                std_ulogic_vector(to_unsigned(8, 11));
            total <= resize(unsigned(armed_length(i)), 11) when
                (resize(unsigned(armed_length(i)), 11) < buffer_bytes) else buffer_bytes;
            remaining(i) <= std_ulogic_vector(total - unsigned(sent(i)));
            in_last_entry(i) <= '1' when (unsigned(remaining(i)) <= unsigned(packet_bytes(i))) else '0';
        end block;
    end generate;

    in_last <= in_last_entry;

    setup_endpoint <= to_integer(unsigned(setup_entry));
    write_allowed <= out_en and (armed or setup_write);

    request_endpoint <= to_integer(unsigned(descriptor.endpoint_number(DEST_WIDTH - 1 downto 0)));
    read_start <= descriptor.start and in_en(request_endpoint) and armed(request_endpoint) and not reading;

    read_command.endpoint_number <= descriptor.endpoint_number;
    read_command.base_address <= std_ulogic_vector(unsigned(endpoint_config(request_endpoint).base_address) +
        resize(unsigned(sent(request_endpoint)), ADDR_WIDTH));
    -- The next packet of the transfer, never past the entry's buffer.
    read_command.length <=
        remaining(request_endpoint)(9 downto 0) when
            (unsigned(remaining(request_endpoint)) < unsigned(packet_bytes(request_endpoint))) else
        packet_bytes(request_endpoint)(9 downto 0);
    read_command.start <= read_start;
    in_busy <= reading;

    out_endpoint <= to_integer(unsigned(out_result.entry(DEST_WIDTH - 1 downto 0)));
    read_endpoint <= to_integer(unsigned(in_report.endpoint_number(DEST_WIDTH - 1 downto 0)));
    read_done_isochronous <= in_report.valid when
        (endpoint_config(read_endpoint).kind = ENDPOINT_KIND_ISOCHRONOUS) else '0';
    in_complete <= in_result.complete or read_done_isochronous;
    in_endpoint <= to_integer(unsigned(in_result.entry(DEST_WIDTH - 1 downto 0))) when
        (in_result.complete = '1') else read_endpoint;
    in_done <= in_complete and in_last_entry(in_endpoint);
    out_complete <= out_result.complete;
    out_overrun <= out_result.overrun;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            armed <= (others => '0');
            armed_length <= (others => (others => '0'));
            sent <= (others => (others => '0'));
            reading <= '0';
        elsif rising_edge(clk) then
            if (out_complete = '1') then
                armed(out_endpoint) <= '0';
            end if;
            if (in_done = '1') then
                armed(in_endpoint) <= '0';
                sent(in_endpoint) <= (others => '0');
            elsif (in_complete = '1') then
                sent(in_endpoint) <= std_ulogic_vector(unsigned(sent(in_endpoint)) +
                    unsigned(packet_bytes(in_endpoint)(9 downto 0)));
            end if;
            if (setup_written = '1') then
                for i in armed'range loop
                    if (endpoint_config(i).endpoint_number = endpoint_config(setup_endpoint).endpoint_number) then
                        armed(i) <= '0';
                    end if;
                end loop;
            end if;
            for i in arm'range loop
                if (arm(i) = '1') then
                    armed(i) <= '1';
                    armed_length(i) <= transfer_length(i);
                    sent(i) <= (others => '0');
                end if;
            end loop;

            if (read_start = '1') then
                reading <= '1';
            elsif (in_report.valid = '1') then
                reading <= '0';
            end if;
        end if;
    end process;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            event <= TRANSFER_EVENT_INIT;
        elsif rising_edge(clk) then
            if (event_ready = '1') then
                event.valid <= '0';
            end if;
            if (out_complete = '1') then
                event.endpoint_number <= out_result.entry;
                event.byte_count <= out_result.byte_count;
                event.zlp <= '1' when (unsigned(out_result.byte_count) = 0) else '0';
                event.overrun <= '0';
                event.setup <= '0';
                event.valid <= '1';
            elsif (out_overrun = '1') then
                event.endpoint_number <= out_result.entry;
                event.byte_count <= (others => '0');
                event.zlp <= '0';
                event.overrun <= '1';
                event.setup <= '0';
                event.valid <= '1';
            elsif (in_done = '1') then
                event.endpoint_number <= std_ulogic_vector(to_unsigned(in_endpoint, event.endpoint_number'length));
                event.byte_count <= (others => '0');
                event.zlp <= '0';
                event.overrun <= '0';
                event.setup <= '0';
                event.valid <= '1';
            elsif (setup_written = '1') then
                event.endpoint_number <= std_ulogic_vector(resize(unsigned(setup_entry), event.endpoint_number'length));
                event.byte_count <= std_ulogic_vector(to_unsigned(8, event.byte_count'length));
                event.zlp <= '0';
                event.overrun <= '0';
                event.setup <= '1';
                event.valid <= '1';
            end if;
        end if;
    end process;

end architecture rtl;
