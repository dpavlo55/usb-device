library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_protocol_tb is
    port (
        clk                    : in  std_ulogic;
        rst_n                  : in  std_ulogic;

        packet_valid           : in  std_ulogic;
        packet_pid             : in  std_ulogic_vector(3 downto 0);
        packet_pid_ok          : in  std_ulogic;
        packet_crc_ok          : in  std_ulogic;
        packet_address         : in  std_ulogic_vector(6 downto 0);
        packet_endpoint        : in  std_ulogic_vector(3 downto 0);
        packet_length          : in  std_ulogic_vector(9 downto 0);

        reset_detected         : in  std_ulogic;
        enable_core            : in  std_ulogic;
        device_address         : in  std_ulogic_vector(6 downto 0);

        endpoint_enable        : in  std_ulogic_vector(15 downto 0);
        endpoint_direction     : in  std_ulogic_vector(15 downto 0);
        endpoint_number        : in  std_ulogic_vector(63 downto 0);
        endpoint_kind          : in  std_ulogic_vector(31 downto 0);
        endpoint_size          : in  std_ulogic_vector(47 downto 0);
        endpoint_armed         : in  std_ulogic_vector(15 downto 0);
        endpoint_stall         : in  std_ulogic_vector(15 downto 0);
        endpoint_double_buffer : in  std_ulogic_vector(15 downto 0);
        endpoint_in_last       : in  std_ulogic_vector(15 downto 0);
        reset_toggle           : in  std_ulogic_vector(15 downto 0);
        in_busy                : in  std_ulogic;

        route_entry            : out std_ulogic_vector(3 downto 0);
        route_accept           : out std_ulogic;
        route_setup            : out std_ulogic;

        tx_valid               : out std_ulogic;
        tx_pid                 : out std_ulogic_vector(3 downto 0);
        setup_received         : out std_ulogic;

        out_complete           : out std_ulogic;
        out_overrun            : out std_ulogic;
        out_entry              : out std_ulogic_vector(3 downto 0);
        out_byte_count         : out std_ulogic_vector(9 downto 0);

        in_request_start       : out std_ulogic;
        in_request_entry       : out std_ulogic_vector(3 downto 0);
        in_complete            : out std_ulogic;
        in_complete_entry      : out std_ulogic_vector(3 downto 0)
    );
end entity;

architecture sim of usb_protocol_tb is

    signal rx_packet : packet_t;
    signal endpoint_config : endpoint_config_array_t(0 to 15)(base_address(9 downto 0));
    signal rx_route : rx_route_t;
    signal tx_packet : packet_t;
    signal tx_pid_byte : std_ulogic_vector(7 downto 0);
    signal out_result : out_result_t;
    signal in_request : packet_descriptor_t;
    signal in_result : in_result_t;

begin

    rx_packet_drive : process (all)
        variable packet_value : packet_t;
    begin
        packet_value := PACKET_INIT;
        packet_value.pid := to_pid(packet_pid);
        packet_value.packet_type := to_packet_type(packet_value.pid);
        packet_value.pid_ok := packet_pid_ok;
        packet_value.crc_ok := packet_crc_ok;
        packet_value.address := packet_address;
        packet_value.endpoint := packet_endpoint;
        packet_value.payload_length := packet_length;
        packet_value.valid := packet_valid;
        rx_packet <= packet_value;
    end process;

    endpoint_config_gen : for i in 0 to 15 generate
        endpoint_config(i).base_address <= (others => '0');
        endpoint_config(i).enable <= endpoint_enable(i);
        endpoint_config(i).direction <= endpoint_direction(i);
        endpoint_config(i).endpoint_number <= endpoint_number(4 * i + 3 downto 4 * i);
        endpoint_config(i).kind <= endpoint_kind(2 * i + 1 downto 2 * i);
        endpoint_config(i).stall <= endpoint_stall(i);
        endpoint_config(i).size <= endpoint_size(3 * i + 2 downto 3 * i);
        endpoint_config(i).double_buffer <= endpoint_double_buffer(i);
        endpoint_config(i).split <= "000";
    end generate;

    usb_protocol_inst : entity work.usb_protocol
        port map (
            clk             => clk,
            rst_n           => rst_n,
            rx_packet       => rx_packet,
            reset_detected  => reset_detected,
            enable_core     => enable_core,
            device_address  => device_address,
            endpoint_config => endpoint_config,
            armed           => endpoint_armed,
            reset_toggle    => reset_toggle,
            in_busy         => in_busy,
            in_last         => endpoint_in_last,
            rx_route        => rx_route,
            tx_packet       => tx_packet,
            setup_received  => setup_received,
            out_result      => out_result,
            in_request      => in_request,
            in_result       => in_result
        );

    route_entry <= rx_route.entry;
    route_accept <= rx_route.accept;
    route_setup <= rx_route.setup;

    tx_valid <= tx_packet.valid;
    tx_pid_byte <= to_pid_byte(tx_packet.pid);
    tx_pid <= tx_pid_byte(3 downto 0);

    out_complete <= out_result.complete;
    out_overrun <= out_result.overrun;
    out_entry <= out_result.entry;
    out_byte_count <= out_result.byte_count;

    in_request_start <= in_request.start;
    in_request_entry <= in_request.endpoint_number;
    in_complete <= in_result.complete;
    in_complete_entry <= in_result.entry;

end architecture;
