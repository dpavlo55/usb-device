library ieee;
use ieee.std_logic_1164.all;

entity usb_packet_serializer_tb is
    port (
        clk           : in  std_ulogic;
        rst_n         : in  std_ulogic;

        packet_tdata  : in  std_ulogic_vector(7 downto 0);
        packet_tkeep  : in  std_ulogic_vector(0 downto 0);
        packet_tdest  : in  std_ulogic_vector(3 downto 0);
        packet_tlast  : in  std_ulogic;
        packet_tvalid : in  std_ulogic;
        packet_tready : out std_ulogic;

        stream_tdata  : out std_ulogic_vector(0 downto 0);
        stream_tkeep  : out std_ulogic_vector(0 downto 0);
        stream_tdest  : out std_ulogic_vector(0 downto 0);
        stream_tlast  : out std_ulogic;
        stream_tvalid : out std_ulogic;
        stream_tready : in  std_ulogic
    );
end entity usb_packet_serializer_tb;

architecture sim of usb_packet_serializer_tb is
begin
    serializer_inst : entity work.usb_packet_serializer
        generic map (
            DATA_WIDTH => 8,
            KEEP_WIDTH => 1,
            DEST_WIDTH => 4
        )
        port map (
            clk           => clk,
            rst_n         => rst_n,
            packet_tdata  => packet_tdata,
            packet_tkeep  => packet_tkeep,
            packet_tdest  => packet_tdest,
            packet_tlast  => packet_tlast,
            packet_tvalid => packet_tvalid,
            packet_tready => packet_tready,
            stream_tdata  => stream_tdata,
            stream_tkeep  => stream_tkeep,
            stream_tdest  => stream_tdest,
            stream_tlast  => stream_tlast,
            stream_tvalid => stream_tvalid,
            stream_tready => stream_tready
        );
end architecture sim;
