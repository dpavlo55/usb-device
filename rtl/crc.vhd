library ieee;
use ieee.std_logic_1164.all;

use ieee.numeric_std.all;

entity crc is
    generic (
        DATA_WIDTH : natural := 8;
        CRC_WIDTH  : natural := 8;
        POLY       : std_logic_vector(CRC_WIDTH - 1 downto 0) := (others => '1')
    );
    port (
        clk        : in  std_ulogic;
        rst_n      : in  std_ulogic;
        tdata      : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        tkeep      : in  std_ulogic_vector((DATA_WIDTH + 7) / 8 - 1 downto 0);
        tlast      : in  std_ulogic;
        tvalid     : in  std_ulogic;
        crc_out    : out std_ulogic_vector(CRC_WIDTH - 1 downto 0);
        crc_valid  : out std_ulogic
    );
end entity crc;

architecture rtl of crc is

    constant SEED : std_ulogic_vector(CRC_WIDTH - 1 downto 0) := (others => '1');

    function reverse_bits(
        data : std_ulogic_vector
    ) return std_ulogic_vector is
        variable reversed : std_ulogic_vector(data'range);
    begin
        for i in data'range loop
            reversed(i) := data(data'low + data'high - i);
        end loop;
        return reversed;
    end function reverse_bits;

    function crc_serial(
        crc  : std_ulogic_vector;
        poly : std_ulogic_vector;
        data : std_ulogic
    ) return std_ulogic_vector is
        variable crc_out  : std_ulogic_vector(crc'range);
        variable feedback : std_ulogic;
    begin
        -- the feedback bit is the MSB of the current CRC XOR'd with the input data
        feedback := crc(crc'high) xor data;

        -- LFSR shift and XOR loop
        for i in 0 to crc'high loop
            if i = 0 then
                -- LSB boundary condition
                if poly(0) = '1' then
                    crc_out(0) := feedback;
                else
                    crc_out(0) := '0';
                end if;
            else
                -- standard shifting and polynomial tap insertion
                if poly(i) = '1' then
                    crc_out(i) := crc(i-1) xor feedback;
                else
                    crc_out(i) := crc(i-1);
                end if;
            end if;
        end loop;

        return crc_out;
    end function crc_serial;

    function crc_parallel(
        crc  : std_ulogic_vector;
        poly : std_ulogic_vector;
        data : std_ulogic_vector;
        keep : std_ulogic_vector
    ) return std_ulogic_vector is
        variable crc_out : std_ulogic_vector(crc'range);
    begin
        crc_out := crc;
        -- Process kept byte lanes LSB-first, including a partial final byte.
        for i in 0 to data'high loop
            if keep(i / 8) = '1' then
                crc_out := crc_serial(crc_out, poly, data(i));
            end if;
        end loop;
        return crc_out;
    end function crc_parallel;

    signal crc_reg : std_ulogic_vector(CRC_WIDTH - 1 downto 0);
    signal tfirst : std_ulogic;

begin

    process (clk, rst_n)
        variable crc_next : std_ulogic_vector(CRC_WIDTH - 1 downto 0);
    begin
        if (rst_n = '0') then
            crc_out <= (others => '0');
            crc_valid <= '0';
            crc_reg <= (others => '0');
            tfirst <= '1';
        elsif rising_edge(clk) then
            if (tvalid = '1') then
                if (tfirst = '1') then
                    crc_valid <= '0';
                    crc_next := crc_parallel(SEED, POLY, tdata, tkeep);
                    tfirst <= '0';
                else
                    crc_next := crc_parallel(crc_reg, POLY, tdata, tkeep);
                end if;

                if (tlast = '1') then
                    crc_next := not crc_next;
                    crc_next := reverse_bits(crc_next);
                    crc_out <= crc_next;
                    crc_valid <= '1';
                    tfirst <= '1';
                end if;

                crc_reg <= crc_next;
            end if;
        end if;
    end process;

end architecture rtl;