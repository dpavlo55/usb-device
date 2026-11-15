library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity usb_ram is
    generic (
        ADDR_WIDTH : natural := 10
    );
    port (
        clk_sys   : in  std_ulogic;
        en_sys    : in  std_ulogic;
        we_sys    : in  std_ulogic_vector(3 downto 0);
        addr_sys  : in  std_ulogic_vector(ADDR_WIDTH - 3 downto 0);
        idata_sys : in  std_ulogic_vector(31 downto 0);
        odata_sys : out std_ulogic_vector(31 downto 0);

        clk_usb   : in  std_ulogic;
        en_usb    : in  std_ulogic;
        we_usb    : in  std_ulogic;
        addr_usb  : in  std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
        idata_usb : in  std_ulogic_vector(7 downto 0);
        odata_usb : out std_ulogic_vector(7 downto 0)
    );
end entity usb_ram;

architecture rtl of usb_ram is

    constant NUMBER_OF_BANKS : natural := 4;

    type bank_data_t is array (0 to NUMBER_OF_BANKS - 1) of
        std_ulogic_vector(7 downto 0);

    signal bank_usb : natural range 0 to NUMBER_OF_BANKS - 1;
    signal read_bank_usb : natural range 0 to NUMBER_OF_BANKS - 1;
    signal usb_bank_en : std_ulogic_vector(NUMBER_OF_BANKS - 1 downto 0);
    signal odata_bank_usb : bank_data_t;

begin

    bank_usb <= to_integer(unsigned(addr_usb(1 downto 0)));

    process (all)
    begin
        usb_bank_en <= (others => '0');
        usb_bank_en(bank_usb) <= en_usb;
    end process;

    -- Read data comes one cycle after the address, so the bank must be delayed too.
    process (clk_usb)
    begin
        if rising_edge(clk_usb) then
            if (en_usb = '1') then
                read_bank_usb <= bank_usb;
            end if;
        end if;
    end process;

    odata_usb <= odata_bank_usb(read_bank_usb);

    bank_gen : for i in 0 to NUMBER_OF_BANKS - 1 generate
        dual_port_ram_inst : entity work.dual_port_ram
            generic map (
                ADDR_WIDTH => ADDR_WIDTH - 2,
                DATA_WIDTH => 8
            )
            port map (
                clk_sys   => clk_sys,
                en_sys    => en_sys,
                we_sys    => we_sys(i),
                addr_sys  => addr_sys,
                idata_sys => idata_sys(8 * i + 7 downto 8 * i),
                odata_sys => odata_sys(8 * i + 7 downto 8 * i),
                clk_usb   => clk_usb,
                en_usb    => usb_bank_en(i),
                we_usb    => we_usb,
                addr_usb  => addr_usb(ADDR_WIDTH - 1 downto 2),
                idata_usb => idata_usb,
                odata_usb => odata_bank_usb(i)
            );
    end generate;

end architecture rtl;
