library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_ram_usb_adapter_tb is
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        base_address  : in std_ulogic_vector(16 * 10 - 1 downto 0);
        write_allowed : in std_ulogic_vector(15 downto 0);

        read_endpoint     : in std_ulogic_vector(3 downto 0);
        read_base_address : in std_ulogic_vector(9 downto 0);
        read_length       : in std_ulogic_vector(9 downto 0);
        read_start        : in std_ulogic;

        out_report_endpoint   : out std_ulogic_vector(3 downto 0);
        out_report_byte_count : out std_ulogic_vector(9 downto 0);
        out_report_accepted   : out std_ulogic;
        out_report_valid      : out std_ulogic;

        in_report_endpoint   : out std_ulogic_vector(3 downto 0);
        in_report_byte_count : out std_ulogic_vector(9 downto 0);
        in_report_accepted   : out std_ulogic;
        in_report_valid      : out std_ulogic;

        rx_payload_tdata  : in  std_ulogic_vector(7 downto 0);
        rx_payload_tkeep  : in  std_ulogic_vector(0 downto 0);
        rx_payload_tdest  : in  std_ulogic_vector(3 downto 0);
        rx_payload_tlast  : in  std_ulogic;
        rx_payload_tvalid : in  std_ulogic;
        rx_payload_tready : out std_ulogic;

        tx_payload_tdata  : out std_ulogic_vector(7 downto 0);
        tx_payload_tkeep  : out std_ulogic_vector(0 downto 0);
        tx_payload_tdest  : out std_ulogic_vector(3 downto 0);
        tx_payload_tlast  : out std_ulogic;
        tx_payload_tvalid : out std_ulogic;
        tx_payload_tready : in  std_ulogic;

        -- SW port of the shared RAM, used by the testbench as a backdoor.
        sys_en    : in  std_ulogic;
        sys_we    : in  std_ulogic_vector(3 downto 0);
        sys_addr  : in  std_ulogic_vector(7 downto 0);
        sys_wdata : in  std_ulogic_vector(31 downto 0);
        sys_rdata : out std_ulogic_vector(31 downto 0)
    );
end entity;

architecture sim of usb_ram_usb_adapter_tb is

    signal base_address_array : vector_array_t(0 to 15)(9 downto 0);
    signal read_command : ram_read_command_t(base_address(9 downto 0));
    signal out_report : packet_report_t;
    signal in_report : packet_report_t;

    signal ram_en : std_ulogic;
    signal ram_we : std_ulogic;
    signal ram_addr : std_ulogic_vector(9 downto 0);
    signal ram_wdata : std_ulogic_vector(7 downto 0);
    signal ram_rdata : std_ulogic_vector(7 downto 0);

begin

    base_address_gen : for i in 0 to 15 generate
        base_address_array(i) <= base_address(10 * i + 9 downto 10 * i);
    end generate;

    read_command.endpoint_number <= read_endpoint;
    read_command.base_address <= read_base_address;
    read_command.length <= read_length;
    read_command.start <= read_start;

    out_report_endpoint <= out_report.endpoint_number;
    out_report_byte_count <= out_report.byte_count;
    out_report_accepted <= out_report.accepted;
    out_report_valid <= out_report.valid;

    in_report_endpoint <= in_report.endpoint_number;
    in_report_byte_count <= in_report.byte_count;
    in_report_accepted <= in_report.accepted;
    in_report_valid <= in_report.valid;

    usb_ram_usb_adapter_inst : entity work.usb_ram_usb_adapter
        generic map (
            ADDR_WIDTH => 10
        )
        port map (
            clk                   => clk,
            rst_n                 => rst_n,
            endpoint_base_address => base_address_array,
            write_allowed         => write_allowed,
            read_command          => read_command,
            out_report            => out_report,
            in_report             => in_report,
            rx_payload_tdata      => rx_payload_tdata,
            rx_payload_tkeep      => rx_payload_tkeep,
            rx_payload_tdest      => rx_payload_tdest,
            rx_payload_tlast      => rx_payload_tlast,
            rx_payload_tvalid     => rx_payload_tvalid,
            rx_payload_tready     => rx_payload_tready,
            tx_payload_tdata      => tx_payload_tdata,
            tx_payload_tkeep      => tx_payload_tkeep,
            tx_payload_tdest      => tx_payload_tdest,
            tx_payload_tlast      => tx_payload_tlast,
            tx_payload_tvalid     => tx_payload_tvalid,
            tx_payload_tready     => tx_payload_tready,
            ram_en                => ram_en,
            ram_we                => ram_we,
            ram_addr              => ram_addr,
            ram_wdata             => ram_wdata,
            ram_rdata             => ram_rdata
        );

    usb_ram_inst : entity work.usb_ram
        generic map (
            ADDR_WIDTH => 10
        )
        port map (
            clk_sys   => clk,
            en_sys    => sys_en,
            we_sys    => sys_we,
            addr_sys  => sys_addr,
            idata_sys => sys_wdata,
            odata_sys => sys_rdata,
            clk_usb   => clk,
            en_usb    => ram_en,
            we_usb    => ram_we,
            addr_usb  => ram_addr,
            idata_usb => ram_wdata,
            odata_usb => ram_rdata
        );

end architecture;
