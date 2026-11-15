library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_bus_state_tb is
    generic (
        SUSPEND_IDLE_CLOCKS       : positive := 300;
        REMOTE_WAKEUP_IDLE_CLOCKS : positive := 500;
        RESUME_DRIVE_CLOCKS       : positive := 200;
        RESUME_GUARD_CLOCKS       : positive := 20
    );
    port (
        clk           : in  std_ulogic;
        rst_n         : in  std_ulogic;

        line_dp       : in  std_ulogic;
        line_dn       : in  std_ulogic;
        attached      : in  std_ulogic;

        packet_valid  : in  std_ulogic;
        packet_pid    : in  std_ulogic_vector(3 downto 0);
        packet_pid_ok : in  std_ulogic;
        packet_crc_ok : in  std_ulogic;

        remote_wakeup : in  std_ulogic;

        suspended     : out std_ulogic;
        resume        : out std_ulogic;
        sof           : out std_ulogic;
        resume_drive  : out std_ulogic
    );
end entity;

architecture sim of usb_bus_state_tb is

    signal usb_line_state : usb_line_state_t;
    signal rx_packet : packet_t;

begin

    usb_line_state <= to_usb_line_state(line_dp, line_dn);

    rx_packet_drive : process (all)
        variable packet_value : packet_t;
    begin
        packet_value := PACKET_INIT;
        packet_value.pid := to_pid(packet_pid);
        packet_value.packet_type := to_packet_type(packet_value.pid);
        packet_value.pid_ok := packet_pid_ok;
        packet_value.crc_ok := packet_crc_ok;
        packet_value.valid := packet_valid;
        rx_packet <= packet_value;
    end process;

    usb_bus_state_inst : entity work.usb_bus_state
        generic map (
            SUSPEND_IDLE_CLOCKS       => SUSPEND_IDLE_CLOCKS,
            REMOTE_WAKEUP_IDLE_CLOCKS => REMOTE_WAKEUP_IDLE_CLOCKS,
            RESUME_DRIVE_CLOCKS       => RESUME_DRIVE_CLOCKS,
            RESUME_GUARD_CLOCKS       => RESUME_GUARD_CLOCKS
        )
        port map (
            clk            => clk,
            rst_n          => rst_n,
            usb_line_state => usb_line_state,
            attached       => attached,
            rx_packet      => rx_packet,
            remote_wakeup  => remote_wakeup,
            suspended      => suspended,
            resume         => resume,
            sof            => sof,
            frame_number   => open,
            resume_drive   => resume_drive
        );

end architecture;
