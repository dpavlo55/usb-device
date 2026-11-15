library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package usb_pkg is

    constant SYNC_BYTE : std_ulogic_vector(7 downto 0) := b"1000_0000";

    type usb_line_state_t is (
        USB_FS_J,        -- Full-Speed idle: D+ HIGH, D- LOW
        USB_FS_K,        -- Full-Speed: D+ LOW, D- HIGH
        USB_FS_SE0,      -- Single-ended zero: D+ LOW, D- LOW
        USB_FS_SE1,      -- Single-ended one: D+ HIGH, D- HIGH (illegal)
        USB_LINE_INVALID -- Unknown, floating, or otherwise invalid input
    );

    type data_valid_t is record
        data  : std_ulogic_vector;
        valid : std_ulogic;
    end record;

    type packet_type_t is (
        TOKEN,
        DATA,
        HANDSHAKE,
        SPECIAL,
        INVALID
    );

    type pid_t is ( -- Binary: | Hex:
        PID_OUT,    -- b"0001" | x"1"
        PID_IN,     -- b"1001" | x"9"
        PID_SOF,    -- b"0101" | x"5"
        PID_SETUP,  -- b"1101" | x"D"
        PID_DATA0,  -- b"0011" | x"3"
        PID_DATA1,  -- b"1011" | x"B"
        PID_ACK,    -- b"0010" | x"2"
        PID_NAK,    -- b"1010" | x"A"
        PID_STALL,  -- b"1110" | x"E"
        PID_PRE,    -- b"1100" | x"C"
        PID_INVALID -- b"xxxx" | x"X"
    );

    type packet_t is record
        packet_type : packet_type_t;
        pid : pid_t;
        pid_ok : std_ulogic;
        address : std_ulogic_vector(6 downto 0);
        endpoint : std_ulogic_vector(3 downto 0);
        frame_number : std_ulogic_vector(10 downto 0);
        payload_length : std_ulogic_vector(9 downto 0);
        crc_ok : std_ulogic;
        valid : std_ulogic;
    end record;

    constant PACKET_INIT : packet_t := (
        packet_type => INVALID,
        pid => PID_INVALID,
        pid_ok => '0',
        address => (others => '0'),
        endpoint => (others => '0'),
        frame_number => (others => '0'),
        payload_length => (others => '0'),
        crc_ok => '0',
        valid => '0'
    );

    type vector_array_t is array (natural range <>) of std_ulogic_vector;

    type packet_descriptor_t is record
        endpoint_number : std_ulogic_vector(3 downto 0);
        start : std_ulogic;
    end record;

    constant PACKET_DESCRIPTOR_INIT : packet_descriptor_t := (
        endpoint_number => (others => '0'),
        start => '0'
    );

    constant ENDPOINT_DIRECTION_IN : std_ulogic := '0';
    constant ENDPOINT_DIRECTION_OUT : std_ulogic := '1';

    constant ENDPOINT_KIND_CONTROL : std_ulogic_vector(1 downto 0) := "00";
    constant ENDPOINT_KIND_ISOCHRONOUS : std_ulogic_vector(1 downto 0) := "11";

    type endpoint_config_t is record
        base_address : std_ulogic_vector;
        enable : std_ulogic;
        direction : std_ulogic;
        endpoint_number : std_ulogic_vector(3 downto 0);
        kind : std_ulogic_vector(1 downto 0);
        stall : std_ulogic;
        size : std_ulogic_vector(2 downto 0);
        double_buffer : std_ulogic;
        split : std_ulogic_vector(2 downto 0);
    end record;

    type rx_route_t is record
        entry : std_ulogic_vector(3 downto 0);
        accept : std_ulogic;
        setup : std_ulogic;
    end record;

    constant RX_ROUTE_INIT : rx_route_t := (
        entry => (others => '0'),
        accept => '0',
        setup => '0'
    );

    type out_result_t is record
        entry : std_ulogic_vector(3 downto 0);
        byte_count : std_ulogic_vector(9 downto 0);
        complete : std_ulogic;
        overrun : std_ulogic;
    end record;

    constant OUT_RESULT_INIT : out_result_t := (
        entry => (others => '0'),
        byte_count => (others => '0'),
        complete => '0',
        overrun => '0'
    );

    type in_result_t is record
        entry : std_ulogic_vector(3 downto 0);
        complete : std_ulogic;
    end record;

    constant IN_RESULT_INIT : in_result_t := (
        entry => (others => '0'),
        complete => '0'
    );

    type endpoint_config_array_t is array (natural range <>) of endpoint_config_t;

    type ram_read_command_t is record
        endpoint_number : std_ulogic_vector(3 downto 0);
        base_address : std_ulogic_vector;
        length : std_ulogic_vector(9 downto 0);
        start : std_ulogic;
    end record;

    type packet_report_t is record
        endpoint_number : std_ulogic_vector(3 downto 0);
        byte_count : std_ulogic_vector(9 downto 0);
        accepted : std_ulogic;
        valid : std_ulogic;
    end record;

    type transfer_event_t is record
        endpoint_number : std_ulogic_vector(3 downto 0);
        byte_count : std_ulogic_vector(9 downto 0);
        zlp : std_ulogic;
        overrun : std_ulogic;
        setup : std_ulogic;
        valid : std_ulogic;
    end record;

    constant TRANSFER_EVENT_INIT : transfer_event_t := (
        endpoint_number => (others => '0'),
        byte_count => (others => '0'),
        zlp => '0',
        overrun => '0',
        setup => '0',
        valid => '0'
    );

    function to_usb_line_state(
        dp : std_ulogic;
        dm : std_ulogic
    ) return usb_line_state_t;

    function to_usb_dp(
        line_state : usb_line_state_t
    ) return std_ulogic;

    function to_usb_dn(
        line_state : usb_line_state_t
    ) return std_ulogic;

    function is_usb_line_state_jk(
        line_state : usb_line_state_t
    ) return boolean;

    function check_pid(
        pid : std_ulogic_vector(7 downto 0)
    ) return std_ulogic;

    function to_packet_type(
        pid : pid_t
    ) return packet_type_t;

    function to_pid(
        pid : std_ulogic_vector(3 downto 0)
    ) return pid_t;

    function check_crc(
        crc_received : std_ulogic_vector;
        crc_expected : std_ulogic_vector
    ) return std_ulogic;

    function to_pid_byte(
        pid : pid_t
    ) return std_ulogic_vector;

end package;

package body usb_pkg is

    function to_usb_line_state(
        dp : std_ulogic;
        dm : std_ulogic
    ) return usb_line_state_t is
        variable line_bits : std_ulogic_vector(1 downto 0);
    begin
        line_bits := dp & dm;
        case line_bits is
            when "10" =>
                return USB_FS_J;
            when "01" =>
                return USB_FS_K;
            when "00" =>
                return USB_FS_SE0;
            when "11" =>
                return USB_FS_SE1;
            when others =>
                return USB_LINE_INVALID;
        end case;
    end function;

    function to_usb_dp(
        line_state : usb_line_state_t
    ) return std_ulogic is
    begin
        case (line_state) is
            when USB_FS_J | USB_FS_SE1 =>
                return '1';
            when others =>
                return '0';
        end case;
    end function;

    function to_usb_dn(
        line_state : usb_line_state_t
    ) return std_ulogic is
    begin
        case (line_state) is
            when USB_FS_K | USB_FS_SE1 =>
                return '1';
            when others =>
                return '0';
        end case;
    end function;

    function is_usb_line_state_jk(
        line_state : usb_line_state_t
    ) return boolean is
    begin
        return (line_state = USB_FS_J or line_state = USB_FS_K);
    end function;

    function check_pid(
        pid : std_ulogic_vector(7 downto 0)
    ) return std_ulogic is
    begin
        if (pid(3 downto 0) = not pid(7 downto 4)) then
            return '1';
        else
            return '0';
        end if;
    end function;

    function to_pid(
        pid : std_ulogic_vector(3 downto 0)
    ) return pid_t is
    begin
        case pid is
            when "0001" => return PID_OUT;
            when "1001" => return PID_IN;
            when "0101" => return PID_SOF;
            when "1101" => return PID_SETUP;
            when "0011" => return PID_DATA0;
            when "1011" => return PID_DATA1;
            when "0010" => return PID_ACK;
            when "1010" => return PID_NAK;
            when "1110" => return PID_STALL;
            when "1100" => return PID_PRE;
            when others => return PID_INVALID;
        end case;
    end function;

    function to_packet_type(
        pid : pid_t
    ) return packet_type_t is
    begin
        case pid is
            when PID_SETUP | PID_IN | PID_OUT | PID_SOF =>
                return TOKEN;
            when PID_DATA0 | PID_DATA1 =>
                return DATA;
            when PID_ACK | PID_NAK | PID_STALL =>
                return HANDSHAKE;
            when PID_PRE =>
                return SPECIAL;
            when others =>
                return INVALID;
        end case;
    end function;

    function check_crc(
        crc_received : std_ulogic_vector;
        crc_expected : std_ulogic_vector
    ) return std_ulogic is
    begin
        if (crc_received = crc_expected) then
            return '1';
        else
            return '0';
        end if;
    end function;

    function to_pid_byte(
        pid : pid_t
    ) return std_ulogic_vector is
    begin
        case pid is
            when PID_OUT =>
                return "11100001";
            when PID_IN =>
                return "01101001";
            when PID_SOF =>
                return "10100101";
            when PID_SETUP =>
                return "00101101";
            when PID_DATA0 =>
                return "11000011";
            when PID_DATA1 =>
                return "01001011";
            when PID_ACK =>
                return "11010010";
            when PID_NAK =>
                return "01011010";
            when PID_STALL =>
                return "00011110";
            when PID_PRE =>
                return "00111100";
            when others =>
                return "00000000"; -- PID_INVALID
        end case;
    end function;

end package body;
