library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_bus_state is
    generic (
        SUSPEND_IDLE_CLOCKS       : positive := 144_000;
        REMOTE_WAKEUP_IDLE_CLOCKS : positive := 240_000;
        RESUME_DRIVE_CLOCKS       : positive := 96_000;
        RESUME_GUARD_CLOCKS       : positive := 480
    );
    port (
        clk            : in  std_ulogic;
        rst_n          : in  std_ulogic;

        usb_line_state : in  usb_line_state_t;
        attached       : in  std_ulogic;
        rx_packet      : in  packet_t;
        remote_wakeup  : in  std_ulogic;

        suspended      : out std_ulogic;
        resume         : out std_ulogic;
        sof            : out std_ulogic;
        frame_number   : out std_ulogic_vector(10 downto 0);
        resume_drive   : out std_ulogic
    );
end entity;

architecture rtl of usb_bus_state is

    constant IDLE_COUNT_MAX : positive := maximum(SUSPEND_IDLE_CLOCKS, REMOTE_WAKEUP_IDLE_CLOCKS);

    signal idle_cnt : natural range 0 to IDLE_COUNT_MAX;
    signal drive_cnt : natural range 0 to RESUME_DRIVE_CLOCKS - 1;
    signal guard_cnt : natural range 0 to RESUME_GUARD_CLOCKS;
    signal wakeup_pending : std_ulogic;
    signal line_ignored : std_ulogic;
    signal activity : std_ulogic;

begin

    -- Own K and the released bus settling are not the host's signaling.
    line_ignored <= '1' when (resume_drive = '1') or (guard_cnt /= 0) else '0';
    activity <= '1' when (usb_line_state /= USB_FS_J) and (line_ignored = '0') else '0';

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            idle_cnt <= 0;
            drive_cnt <= 0;
            guard_cnt <= 0;
            wakeup_pending <= '0';
            suspended <= '0';
            resume <= '0';
            resume_drive <= '0';
        elsif rising_edge(clk) then
            resume <= '0';

            if (guard_cnt /= 0) then
                guard_cnt <= guard_cnt - 1;
            end if;

            if (resume_drive = '1') then
                if (drive_cnt = RESUME_DRIVE_CLOCKS - 1) then
                    resume_drive <= '0';
                    guard_cnt <= RESUME_GUARD_CLOCKS;
                else
                    drive_cnt <= drive_cnt + 1;
                end if;
            end if;

            if (attached = '0') then
                idle_cnt <= 0;
                drive_cnt <= 0;
                guard_cnt <= 0;
                wakeup_pending <= '0';
                suspended <= '0';
                resume_drive <= '0';
            elsif (activity = '1') then
                idle_cnt <= 0;
                wakeup_pending <= '0';
                if (suspended = '1') then
                    suspended <= '0';
                    resume <= '1';
                end if;
            else
                if (idle_cnt /= IDLE_COUNT_MAX) then
                    idle_cnt <= idle_cnt + 1;
                end if;

                if (idle_cnt = SUSPEND_IDLE_CLOCKS - 1) then
                    suspended <= '1';
                end if;

                if (remote_wakeup = '1') and (suspended = '1') then
                    wakeup_pending <= '1';
                end if;

                if (wakeup_pending = '1') and (idle_cnt >= REMOTE_WAKEUP_IDLE_CLOCKS - 1) then
                    wakeup_pending <= '0';
                    resume_drive <= '1';
                    drive_cnt <= 0;
                end if;
            end if;
        end if;
    end process;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            sof <= '0';
            frame_number <= (others => '0');
        elsif rising_edge(clk) then
            sof <= '0';
            if (rx_packet.valid = '1') and (rx_packet.pid = PID_SOF) and
                    (rx_packet.pid_ok = '1') and (rx_packet.crc_ok = '1') then
                sof <= '1';
                frame_number <= rx_packet.frame_number;
            end if;
        end if;
    end process;

end architecture;
