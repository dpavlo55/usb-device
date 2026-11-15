library ieee;
use ieee.std_logic_1164.all;

-- The bus for the behavioral reference device: no RTL, only the resolved D+/D- lines.
-- Same clock/reset and host ports as usb_top_uvm_tb, so the environment is unchanged.
entity usb_reference_tb is
    port (
        clk_sys     : in  std_ulogic;
        rst_sys_n   : in  std_ulogic;
        clk_usb     : in  std_ulogic;
        rst_usb_n   : in  std_ulogic;
        usb_vbus    : in  std_ulogic := '0';
        usb_pu_dev  : in  std_ulogic := '0';
        usb_dp_host : in  std_logic := 'Z';
        usb_dn_host : in  std_logic := 'Z';
        usb_dp_dev  : in  std_logic := 'Z';
        usb_dn_dev  : in  std_logic := 'Z';
        usb_pu      : out std_ulogic;
        usb_dp      : out std_logic;
        usb_dn      : out std_logic
    );
end entity;

architecture sim of usb_reference_tb is

    signal usb_dp_bus : std_logic;
    signal usb_dn_bus : std_logic;

begin

    usb_dp_bus <= usb_dp_host;
    usb_dn_bus <= usb_dn_host;
    usb_dp_bus <= usb_dp_dev;
    usb_dn_bus <= usb_dn_dev;
    usb_dp_bus <= 'H' when usb_pu_dev = '1' else 'L';
    usb_dn_bus <= 'L';

    usb_dp <= usb_dp_bus;
    usb_dn <= usb_dn_bus;
    usb_pu <= usb_pu_dev;

end architecture;
