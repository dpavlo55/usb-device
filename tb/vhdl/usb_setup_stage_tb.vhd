library ieee;
use ieee.std_logic_1164.all;

entity usb_setup_stage_tb is
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        routed_tdata  : in  std_ulogic_vector(7 downto 0);
        routed_tkeep  : in  std_ulogic_vector(0 downto 0);
        routed_tdest  : in  std_ulogic_vector(4 downto 0);
        routed_tlast  : in  std_ulogic;
        routed_tvalid : in  std_ulogic;
        routed_tready : out std_ulogic;

        setup_received : in  std_ulogic;
        setup_writing  : out std_ulogic;
        setup_entry    : out std_ulogic_vector(3 downto 0);
        setup_written  : out std_ulogic;

        write_tdata  : out std_ulogic_vector(7 downto 0);
        write_tkeep  : out std_ulogic_vector(0 downto 0);
        write_tdest  : out std_ulogic_vector(3 downto 0);
        write_tlast  : out std_ulogic;
        write_tvalid : out std_ulogic;
        write_tready : in  std_ulogic
    );
end entity;

architecture sim of usb_setup_stage_tb is
begin

    usb_setup_stage_inst : entity work.usb_setup_stage
        port map (
            clk            => clk,
            rst_n          => rst_n,
            routed_tdata   => routed_tdata,
            routed_tkeep   => routed_tkeep,
            routed_tdest   => routed_tdest,
            routed_tlast   => routed_tlast,
            routed_tvalid  => routed_tvalid,
            routed_tready  => routed_tready,
            setup_received => setup_received,
            setup_writing  => setup_writing,
            setup_entry    => setup_entry,
            setup_written  => setup_written,
            write_tdata    => write_tdata,
            write_tkeep    => write_tkeep,
            write_tdest    => write_tdest,
            write_tlast    => write_tlast,
            write_tvalid   => write_tvalid,
            write_tready   => write_tready
        );

end architecture;
