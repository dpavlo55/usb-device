library ieee;
use ieee.std_logic_1164.all;

use work.usb_device_regs_pkg.USB_DEVICE_REGS_MIN_ADDR_WIDTH;

entity usb_top_uvm_tb is
    port (
        clk_sys      : in  std_ulogic;
        rst_sys_n    : in  std_ulogic;
        clk_usb      : in  std_ulogic;
        rst_usb_n    : in  std_ulogic;

        axil_awaddr  : in  std_logic_vector(USB_DEVICE_REGS_MIN_ADDR_WIDTH - 1 downto 0);
        axil_awprot  : in  std_logic_vector(2 downto 0);
        axil_awvalid : in  std_logic;
        axil_awready : out std_logic;
        axil_wdata   : in  std_logic_vector(31 downto 0);
        axil_wstrb   : in  std_logic_vector(3 downto 0);
        axil_wvalid  : in  std_logic;
        axil_wready  : out std_logic;
        axil_bresp   : out std_logic_vector(1 downto 0);
        axil_bvalid  : out std_logic;
        axil_bready  : in  std_logic;
        axil_araddr  : in  std_logic_vector(USB_DEVICE_REGS_MIN_ADDR_WIDTH - 1 downto 0);
        axil_arprot  : in  std_logic_vector(2 downto 0);
        axil_arvalid : in  std_logic;
        axil_arready : out std_logic;
        axil_rdata   : out std_logic_vector(31 downto 0);
        axil_rresp   : out std_logic_vector(1 downto 0);
        axil_rvalid  : out std_logic;
        axil_rready  : in  std_logic;

        usb_vbus     : in  std_ulogic := '1';
        usb_pu       : out std_ulogic;
        irq          : out std_ulogic;
        usb_dp_host  : in  std_logic := 'Z';
        usb_dn_host  : in  std_logic := 'Z';
        usb_dp       : out std_logic;
        usb_dn       : out std_logic
    );
end entity;

architecture sim of usb_top_uvm_tb is

    signal usb_dp_bus : std_logic;
    signal usb_dn_bus : std_logic;
    signal pull_up    : std_ulogic;

begin

    -- The bus resolves the host drive, the DUT drive, the device's 1.5 kOhm D+ pull-up
    -- (weak '1' while attached) and the host's 15 kOhm pull-downs (weak '0').
    usb_dp_bus <= usb_dp_host;
    usb_dn_bus <= usb_dn_host;
    usb_dp_bus <= 'H' when pull_up = '1' else 'L';
    usb_dn_bus <= 'L';

    usb_dp <= usb_dp_bus;
    usb_dn <= usb_dn_bus;
    usb_pu <= pull_up;

    usb_top_inst : entity work.usb_top
        port map (
            clk_sys      => clk_sys,
            rst_sys_n    => rst_sys_n,
            clk_usb      => clk_usb,
            rst_usb_n    => rst_usb_n,
            axil_awaddr  => axil_awaddr,
            axil_awprot  => axil_awprot,
            axil_awvalid => axil_awvalid,
            axil_awready => axil_awready,
            axil_wdata   => axil_wdata,
            axil_wstrb   => axil_wstrb,
            axil_wvalid  => axil_wvalid,
            axil_wready  => axil_wready,
            axil_bresp   => axil_bresp,
            axil_bvalid  => axil_bvalid,
            axil_bready  => axil_bready,
            axil_araddr  => axil_araddr,
            axil_arprot  => axil_arprot,
            axil_arvalid => axil_arvalid,
            axil_arready => axil_arready,
            axil_rdata   => axil_rdata,
            axil_rresp   => axil_rresp,
            axil_rvalid  => axil_rvalid,
            axil_rready  => axil_rready,
            usb_vbus     => usb_vbus,
            usb_pu       => pull_up,
            irq          => irq,
            usb_dp       => usb_dp_bus,
            usb_dn       => usb_dn_bus
        );

end architecture;
