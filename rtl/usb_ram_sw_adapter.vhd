library ieee;
use ieee.std_logic_1164.all;

entity usb_ram_sw_adapter is
    generic (
        ADDR_WIDTH : natural := 10
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        ram_req       : in  std_ulogic;
        ram_addr      : in  std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
        ram_req_is_wr : in  std_ulogic;
        ram_wr_data   : in  std_ulogic_vector(31 downto 0);
        ram_wr_biten  : in  std_ulogic_vector(31 downto 0);
        ram_rd_ack    : out std_ulogic;
        ram_rd_data   : out std_ulogic_vector(31 downto 0);
        ram_wr_ack    : out std_ulogic;

        bank_en    : out std_ulogic;
        bank_we    : out std_ulogic_vector(3 downto 0);
        bank_addr  : out std_ulogic_vector(ADDR_WIDTH - 3 downto 0);
        bank_wdata : out std_ulogic_vector(31 downto 0);
        bank_rdata : in  std_ulogic_vector(31 downto 0)
    );
end entity usb_ram_sw_adapter;

architecture rtl of usb_ram_sw_adapter is

    constant NUMBER_OF_BANKS : natural := 4;

    signal rd_req : std_ulogic;
    signal wr_req : std_ulogic;

begin

    rd_req <= ram_req and not ram_req_is_wr;
    wr_req <= ram_req and ram_req_is_wr;
    bank_en <= ram_req;
    bank_addr <= ram_addr(ADDR_WIDTH - 1 downto 2);
    bank_wdata <= ram_wr_data;

    -- Byte lane i of the 32-bit SW word is stored in bank i.
    -- Banks are byte-wide, so any enabled bit writes the whole byte.
    we_gen : for i in 0 to NUMBER_OF_BANKS - 1 generate
        bank_we(i) <= wr_req and (or ram_wr_biten(8 * i + 7 downto 8 * i));
    end generate;

    ram_wr_ack <= wr_req;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            ram_rd_ack <= '0';
        elsif rising_edge(clk) then
            ram_rd_ack <= rd_req;
        end if;
    end process;

    ram_rd_data <= bank_rdata;

end architecture rtl;
