library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;

entity usb_phy_rx is
    port (
        clk                  : in  std_ulogic;
        rst_n                : in  std_ulogic;

        usb_dp_i             : in  std_logic;
        usb_dn_i             : in  std_logic;
        tx_active            : in  std_ulogic;

        usb_line_state       : out usb_line_state_t;
        usb_line_state_valid : out std_ulogic
    );
end entity usb_phy_rx;

architecture rtl of usb_phy_rx is

    constant SAMPLE_POINT : natural := 1;

    signal usb_dp_e      : std_ulogic;
    signal sample_cnt    : unsigned(1 downto 0);
    signal sample_cnt_en : std_ulogic;

    signal usb_dp_d      : std_ulogic;
    signal usb_dn_d      : std_ulogic;

begin

    usb_dp_edge_detector : entity work.edge_detector
        generic map (
            REG_EN => false
        )
        port map (
            clk    => clk,
            sig_in => usb_dp_i,
            sig_re => open,
            sig_fe => open,
            sig_e  => usb_dp_e
        );

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            sample_cnt <= (others => '0');
            sample_cnt_en <= '0';
        elsif rising_edge(clk) then
            if (usb_dp_e = '1') then
                sample_cnt <= (others => '0');
                sample_cnt_en <= '1';
            elsif (sample_cnt_en = '1') then
                sample_cnt <= sample_cnt + 1;
            else
                sample_cnt <= (others => '0');
            end if;
        end if;
    end process;

    -- delay the sampled USB D+ and D- signals to compensate edge detector delay
    process (clk)
    begin
        if rising_edge(clk) then
            usb_dp_d <= usb_dp_i;
            usb_dn_d <= usb_dn_i;
        end if;
    end process;

    -- sample the USB D+ and D- signals based on the sample counter
    process (clk, rst_n)
        variable usb_dp_sampled : std_ulogic;
        variable usb_dn_sampled : std_ulogic;
    begin
        if (rst_n = '0') then
            usb_line_state <= USB_FS_J;
            usb_line_state_valid <= '0';
        elsif rising_edge(clk) then
            usb_line_state_valid <= '0';
            if (sample_cnt = SAMPLE_POINT) then
                usb_dp_sampled := usb_dp_d;
                usb_dn_sampled := usb_dn_d;
                if (tx_active = '1') then
                    usb_line_state <= USB_FS_J;
                else
                    usb_line_state <= to_usb_line_state(
                        usb_dp_sampled, usb_dn_sampled);
                end if;
                usb_line_state_valid <= '1';
            end if;
        end if;
    end process;

end architecture;
