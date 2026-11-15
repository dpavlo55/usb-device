library ieee;
use ieee.std_logic_1164.all;

entity crc5_tb is
    port (
        clk        : in  std_ulogic;
        rst_n      : in  std_ulogic;
        tdata      : in  std_ulogic_vector(10 downto 0);
        tkeep      : in  std_ulogic_vector(1 downto 0);
        tvalid     : in  std_ulogic;
        tlast      : in  std_ulogic;
        crc_out    : out std_ulogic_vector(4 downto 0);
        crc_valid  : out std_ulogic
    );
end entity;

architecture sim of crc5_tb is
begin
    dut : entity work.crc
        generic map (
            DATA_WIDTH => 11,
            CRC_WIDTH  => 5,
            POLY       => "00101"
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