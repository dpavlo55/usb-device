library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_packet_parser_tb is
    port (
        clk             : in  std_ulogic;
        rst_n           : in  std_ulogic;

        packet_tdata    : in  std_ulogic_vector(7 downto 0);
        packet_tkeep    : in  std_ulogic_vector(0 downto 0);
        packet_tdest    : in  std_ulogic_vector(3 downto 0);
        packet_tlast    : in  std_ulogic;
        packet_tvalid   : in  std_ulogic;
        packet_tready   : out std_ulogic;

        payload_tdata   : out std_ulogic_vector(7 downto 0);
        payload_tkeep   : out std_ulogic_vector(0 downto 0);
        payload_tdest   : out std_ulogic_vector(3 downto 0);
        payload_tlast   : out std_ulogic;
        payload_tvalid  : out std_ulogic;
        payload_tready  : in  std_ulogic;

        packet_valid    : out std_ulogic;
        packet_crc_ok   : out std_ulogic;
        packet_payload_length : out std_ulogic_vector(9 downto 0)
    );
end entity;

architecture sim of usb_packet_parser_tb is
    signal packet_record : packet_t := PACKET_INIT;

begin

    packet_valid <= packet_record.valid;
    packet_crc_ok <= packet_record.crc_ok;
    packet_payload_length <= packet_record.payload_length;

    usb_packet_parser_inst : entity work.usb_packet_parser
        port map (
            clk            => clk,
            rst_n          => rst_n,
            packet_tdata   => packet_tdata,
            packet_tkeep   => packet_tkeep,
            packet_tdest   => packet_tdest,
            packet_tlast   => packet_tlast,
            packet_tvalid  => packet_tvalid,
            packet_tready  => packet_tready,
            payload_tdata  => payload_tdata,
            payload_tkeep  => payload_tkeep,
            payload_tdest  => payload_tdest,
            payload_tlast  => payload_tlast,
            payload_tvalid => payload_tvalid,
            payload_tready => payload_tready,
            packet         => packet_record
        );

end architecture;