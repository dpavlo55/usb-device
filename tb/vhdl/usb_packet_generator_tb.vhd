library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_packet_generator_tb is
    port (
        clk           : in std_ulogic;
        rst_n         : in std_ulogic;

        packet_pid     : in std_ulogic_vector(3 downto 0);
        packet_valid   : in std_ulogic;
        payload_length : in std_ulogic_vector(9 downto 0);

        payload_tdata  : in std_ulogic_vector(7 downto 0);
        payload_tkeep  : in std_ulogic_vector(0 downto 0);
        payload_tdest  : in std_ulogic_vector(3 downto 0);
        payload_tlast  : in std_ulogic;
        payload_tvalid : in std_ulogic;
        payload_tready : out std_ulogic;

        packet_tdata   : out std_ulogic_vector(7 downto 0);
        packet_tkeep   : out std_ulogic_vector(0 downto 0);
        packet_tdest   : out std_ulogic_vector(3 downto 0);
        packet_tlast   : out std_ulogic;
        packet_tvalid  : out std_ulogic;
        packet_tready  : in std_ulogic
    );
end entity;

architecture sim of usb_packet_generator_tb is

    signal generator_packet : packet_t := PACKET_INIT;

begin

    -- Resolve scalar simulation controls through the package helpers rather
    -- than relying on simulator-specific VHDL enum ordinals.
    generator_packet_drive : process(all)
        variable packet_value : packet_t;
    begin
        packet_value := PACKET_INIT;
        packet_value.pid := to_pid(packet_pid);
        packet_value.packet_type := to_packet_type(packet_value.pid);
        packet_value.pid_ok := check_pid((not packet_pid) & packet_pid);
        packet_value.payload_length := payload_length;
        packet_value.valid := packet_valid;
        generator_packet <= packet_value;
    end process;

    packet_generator_inst : entity work.usb_packet_generator
        port map (
            clk            => clk,
            rst_n          => rst_n,
            packet         => generator_packet,

            payload_tdata  => payload_tdata,
            payload_tkeep  => payload_tkeep,
            payload_tdest  => payload_tdest,
            payload_tlast  => payload_tlast,
            payload_tvalid => payload_tvalid,
            payload_tready => payload_tready,

            packet_tdata   => packet_tdata,
            packet_tkeep   => packet_tkeep,
            packet_tdest   => packet_tdest,
            packet_tlast   => packet_tlast,
            packet_tvalid  => packet_tvalid,
            packet_tready  => packet_tready
        );

end architecture;