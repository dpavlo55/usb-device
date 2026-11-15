library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dual_port_ram is
    generic (
        ADDR_WIDTH : natural := 10;
        DATA_WIDTH : natural := 8
    );
    port (
        clk_sys   : in  std_ulogic;
        en_sys    : in  std_ulogic;
        we_sys    : in  std_ulogic;
        addr_sys  : in  std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
        idata_sys : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        odata_sys : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);

        clk_usb   : in  std_ulogic;
        en_usb    : in  std_ulogic;
        we_usb    : in  std_ulogic;
        addr_usb  : in  std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
        idata_usb : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        odata_usb : out std_ulogic_vector(DATA_WIDTH - 1 downto 0)
    );
end entity dual_port_ram;

architecture rtl of dual_port_ram is

    type ram_t is array (0 to 2 ** ADDR_WIDTH - 1) of
        std_logic_vector(DATA_WIDTH - 1 downto 0);

    shared variable ram : ram_t;

begin

    process (clk_sys)
    begin
        if rising_edge(clk_sys) then
            if (en_sys = '1') then
                odata_sys <= ram(to_integer(unsigned(addr_sys)));
                if (we_sys = '1') then
                    ram(to_integer(unsigned(addr_sys))) := idata_sys;
                end if;
            end if;
        end if;
    end process;

    process (clk_usb)
    begin
        if rising_edge(clk_usb) then
            if (en_usb = '1') then
                odata_usb <= ram(to_integer(unsigned(addr_usb)));
                if (we_usb = '1') then
                    ram(to_integer(unsigned(addr_usb))) := idata_usb;
                end if;
            end if;
        end if;
    end process;

end architecture rtl;
