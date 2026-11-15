library ieee;
use ieee.std_logic_1164.all;

use work.usb_pkg.all;

entity usb_cdc is
    generic (
        ADDR_WIDTH : natural := 10;
        DEST_WIDTH : natural := 4
    );
    port (
        clk_sys   : in std_ulogic;
        rst_sys_n : in std_ulogic;
        clk_usb   : in std_ulogic;
        rst_usb_n : in std_ulogic;

        sys_enable_core     : in  std_ulogic;
        sys_endpoint_config : in  endpoint_config_array_t(0 to 2 ** DEST_WIDTH - 1)(
            base_address(ADDR_WIDTH - 1 downto 0));
        sys_transfer_length : in  vector_array_t(0 to 2 ** DEST_WIDTH - 1)(9 downto 0);
        sys_arm             : in  std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        sys_device_address  : in  std_ulogic_vector(6 downto 0);
        sys_reset_toggle    : in  std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        sys_event           : out transfer_event_t;
        sys_enable_pu       : in  std_ulogic;
        sys_remote_wakeup   : in  std_ulogic;
        sys_suspended       : out std_ulogic;
        sys_resume          : out std_ulogic;
        sys_sof             : out std_ulogic;
        sys_frame_number    : out std_ulogic_vector(10 downto 0);

        usb_enable_core     : out std_ulogic;
        usb_endpoint_config : out endpoint_config_array_t(0 to 2 ** DEST_WIDTH - 1)(
            base_address(ADDR_WIDTH - 1 downto 0));
        usb_transfer_length : out vector_array_t(0 to 2 ** DEST_WIDTH - 1)(9 downto 0);
        usb_arm             : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        usb_device_address  : out std_ulogic_vector(6 downto 0);
        usb_reset_toggle    : out std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        usb_event           : in  transfer_event_t;
        usb_event_ready     : out std_ulogic;
        usb_attached        : out std_ulogic;
        usb_remote_wakeup   : out std_ulogic;
        usb_suspended       : in  std_ulogic;
        usb_resume          : in  std_ulogic;
        usb_sof             : in  std_ulogic;
        usb_frame_number    : in  std_ulogic_vector(10 downto 0)
    );
end entity usb_cdc;

architecture rtl of usb_cdc is

    constant EVENT_WIDTH : natural := 17;

    signal usb_event_data : std_ulogic_vector(EVENT_WIDTH - 1 downto 0);
    signal sys_event_data : std_ulogic_vector(EVENT_WIDTH - 1 downto 0);

    signal sent_device_address : std_ulogic_vector(6 downto 0);
    signal device_address_changed : std_ulogic;
    signal device_address_ready : std_ulogic;

begin

    enable_core_sync_inst : entity work.cdc_sync_single
        port map (
            clk     => clk_usb,
            sig_in  => sys_enable_core,
            sig_out => usb_enable_core
        );

    endpoint_gen : for i in 0 to 2 ** DEST_WIDTH - 1 generate

        enable_sync_inst : entity work.cdc_sync_single
            port map (
                clk     => clk_usb,
                sig_in  => sys_endpoint_config(i).enable,
                sig_out => usb_endpoint_config(i).enable
            );

        -- Software changes these only while the endpoint is disabled, so they
        -- have settled before the synchronized enable rises.
        usb_endpoint_config(i).base_address <= sys_endpoint_config(i).base_address;
        usb_endpoint_config(i).direction <= sys_endpoint_config(i).direction;
        usb_endpoint_config(i).endpoint_number <= sys_endpoint_config(i).endpoint_number;
        usb_endpoint_config(i).kind <= sys_endpoint_config(i).kind;
        usb_endpoint_config(i).size <= sys_endpoint_config(i).size;
        usb_endpoint_config(i).double_buffer <= sys_endpoint_config(i).double_buffer;
        usb_endpoint_config(i).split <= sys_endpoint_config(i).split;

        stall_sync_inst : entity work.cdc_sync_single
            port map (
                clk     => clk_usb,
                sig_in  => sys_endpoint_config(i).stall,
                sig_out => usb_endpoint_config(i).stall
            );

        reset_toggle_pulse_inst : entity work.cdc_pulse
            port map (
                clk_src   => clk_sys,
                rst_src_n => rst_sys_n,
                src_pulse => sys_reset_toggle(i),
                clk_dst   => clk_usb,
                rst_dst_n => rst_usb_n,
                dst_pulse => usb_reset_toggle(i)
            );

        -- Written together with arm and not changed while armed, so it has
        -- settled before the arm pulse arrives.
        usb_transfer_length(i) <= sys_transfer_length(i);

        arm_pulse_inst : entity work.cdc_pulse
            port map (
                clk_src   => clk_sys,
                rst_src_n => rst_sys_n,
                src_pulse => sys_arm(i),
                clk_dst   => clk_usb,
                rst_dst_n => rst_usb_n,
                dst_pulse => usb_arm(i)
            );

    end generate;

    usb_event_data <= usb_event.endpoint_number & usb_event.byte_count & usb_event.zlp & usb_event.overrun & usb_event.setup;

    event_handshake_inst : entity work.cdc_handshake
        generic map (
            DATA_WIDTH => EVENT_WIDTH
        )
        port map (
            clk_src   => clk_usb,
            rst_src_n => rst_usb_n,
            src_data  => usb_event_data,
            src_valid => usb_event.valid,
            src_ready => usb_event_ready,
            clk_dst   => clk_sys,
            rst_dst_n => rst_sys_n,
            dst_data  => sys_event_data,
            dst_valid => sys_event.valid
        );

    sys_event.endpoint_number <= sys_event_data(16 downto 13);
    sys_event.byte_count <= sys_event_data(12 downto 3);
    sys_event.zlp <= sys_event_data(2);
    sys_event.overrun <= sys_event_data(1);
    sys_event.setup <= sys_event_data(0);

    -- The newest address is sent; a change during a crossing follows once it ends.
    device_address_changed <= '1' when (sys_device_address /= sent_device_address) else '0';

    process (clk_sys, rst_sys_n)
    begin
        if (rst_sys_n = '0') then
            sent_device_address <= (others => '0');
        elsif rising_edge(clk_sys) then
            if (device_address_changed = '1' and device_address_ready = '1') then
                sent_device_address <= sys_device_address;
            end if;
        end if;
    end process;

    device_address_handshake_inst : entity work.cdc_handshake
        generic map (
            DATA_WIDTH => 7
        )
        port map (
            clk_src   => clk_sys,
            rst_src_n => rst_sys_n,
            src_data  => sys_device_address,
            src_valid => device_address_changed,
            src_ready => device_address_ready,
            clk_dst   => clk_usb,
            rst_dst_n => rst_usb_n,
            dst_data  => usb_device_address,
            dst_valid => open
        );

    enable_pu_sync_inst : entity work.cdc_sync_single
        port map (
            clk     => clk_usb,
            sig_in  => sys_enable_pu,
            sig_out => usb_attached
        );

    remote_wakeup_pulse_inst : entity work.cdc_pulse
        port map (
            clk_src   => clk_sys,
            rst_src_n => rst_sys_n,
            src_pulse => sys_remote_wakeup,
            clk_dst   => clk_usb,
            rst_dst_n => rst_usb_n,
            dst_pulse => usb_remote_wakeup
        );

    suspended_sync_inst : entity work.cdc_sync_single
        port map (
            clk     => clk_sys,
            sig_in  => usb_suspended,
            sig_out => sys_suspended
        );

    resume_pulse_inst : entity work.cdc_pulse
        port map (
            clk_src   => clk_usb,
            rst_src_n => rst_usb_n,
            src_pulse => usb_resume,
            clk_dst   => clk_sys,
            rst_dst_n => rst_sys_n,
            dst_pulse => sys_resume
        );

    sof_pulse_inst : entity work.cdc_pulse
        port map (
            clk_src   => clk_usb,
            rst_src_n => rst_usb_n,
            src_pulse => usb_sof,
            clk_dst   => clk_sys,
            rst_dst_n => rst_sys_n,
            dst_pulse => sys_sof
        );

    -- Updated together with usb_sof and then stable for a frame (1 ms), so it has
    -- settled before the SOF pulse arrives.
    process (clk_sys, rst_sys_n)
    begin
        if (rst_sys_n = '0') then
            sys_frame_number <= (others => '0');
        elsif rising_edge(clk_sys) then
            if (sys_sof = '1') then
                sys_frame_number <= usb_frame_number;
            end if;
        end if;
    end process;

end architecture rtl;
