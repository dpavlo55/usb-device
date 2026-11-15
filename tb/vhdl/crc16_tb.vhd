library ieee;
use ieee.std_logic_1164.all;

entity crc16_tb is
    port (
        clk        : in  std_ulogic;
        rst_n      : in  std_ulogic;
        tdata      : in  std_ulogic_vector(7 downto 0);
        tkeep      : in  std_ulogic_vector(0 downto 0);
        tvalid     : in  std_ulogic;
        tlast      : in  std_ulogic;
        crc_out    : out std_ulogic_vector(15 downto 0);
        crc_valid  : out std_ulogic
    );
end entity;

architecture sim of crc16_tb is
begin
    dut : entity work.crc
        generic map (
            DATA_WIDTH => 8,
            CRC_WIDTH  => 16,
            POLY       => "1000000000000101"
        )
        port map (
            clk        => clk,
            rst_n      => rst_n,
            tdata      => tdata,
            tkeep      => tkeep,
            tvalid     => tvalid,
            tlast      => tlast,
            crc_out    => crc_out,
            crc_valid  => crc_valid
        );
end architecture;