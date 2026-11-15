library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;

entity usb_turnaround_tb is
    port (
        clk         : in  std_ulogic;
        rst_n       : in  std_ulogic;

        usb_dp_host : in  std_logic;
        usb_dn_host : in  std_logic;
        usb_dp      : out std_logic;
        usb_dn      : out std_logic;
        usb_oe      : out std_ulogic;

        rx_packet_valid : out std_ulogic;
        tx_packet_valid : out std_ulogic;

        in_length       : in  std_ulogic_vector(9 downto 0);
        payload_latency : in  std_ulogic_vector(5 downto 0)
    );
end entity;

architecture sim of usb_turnaround_tb is

    signal usb_dp_bus : std_logic;
    signal usb_dn_bus : std_logic;

    signal usb_dp_i : std_ulogic;
    signal usb_dn_i : std_ulogic;
    signal usb_line_state : usb_line_state_t;
    signal usb_line_state_valid : std_ulogic;

    signal rx_packet_tdata : std_ulogic_vector(7 downto 0);
    signal rx_packet_tkeep : std_ulogic_vector(0 downto 0);
    signal rx_packet_tdest : std_ulogic_vector(3 downto 0);
    signal rx_packet_tlast : std_ulogic;
    signal rx_packet_tvalid : std_ulogic;
    signal rx_packet_tready : std_ulogic;
    signal rx_packet : packet_t;

    signal tx_packet : packet_t;
    signal tx_payload_tdata : std_ulogic_vector(7 downto 0);
    signal tx_payload_tkeep : std_ulogic_vector(0 downto 0);
    signal tx_payload_tlast : std_ulogic;
    signal tx_payload_tvalid : std_ulogic;
    signal tx_payload_tready : std_ulogic;

    signal tx_packet_tdata : std_ulogic_vector(7 downto 0);
    signal tx_packet_tkeep : std_ulogic_vector(0 downto 0);
    signal tx_packet_tdest : std_ulogic_vector(3 downto 0);
    signal tx_packet_tlast : std_ulogic;
    signal tx_packet_tvalid : std_ulogic;
    signal tx_packet_tready : std_ulogic;

    signal serial_tdata : std_ulogic_vector(0 downto 0);
    signal serial_tkeep : std_ulogic_vector(0 downto 0);
    signal serial_tdest : std_ulogic_vector(0 downto 0);
    signal serial_tlast : std_ulogic;
    signal serial_tvalid : std_ulogic;
    signal serial_tready : std_ulogic;

    signal usb_oe_dev : std_ulogic;
    signal usb_dp_dev : std_ulogic;
    signal usb_dn_dev : std_ulogic;

    signal payload_remaining : unsigned(9 downto 0);
    signal payload_delay : unsigned(5 downto 0);
    signal payload_fire : std_ulogic;

begin

    usb_dp_bus <= usb_dp_host;
    usb_dn_bus <= usb_dn_host;
    usb_dp_bus <= usb_dp_dev when usb_oe_dev = '1' else 'Z';
    usb_dn_bus <= usb_dn_dev when usb_oe_dev = '1' else 'Z';
    usb_dp_bus <= 'H';
    usb_dn_bus <= 'L';
    usb_dp <= usb_dp_bus;
    usb_dn <= usb_dn_bus;
    usb_oe <= usb_oe_dev;
    rx_packet_valid <= rx_packet.valid;
    tx_packet_valid <= tx_packet.valid;

    usb_dp_sync_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk,
            sig_in  => to_X01Z(usb_dp_bus),
            sig_out => usb_dp_i
        );

    usb_dn_sync_inst : entity work.cdc_sync_single
        generic map (
            CDC_STAGES => 2
        )
        port map (
            clk     => clk,
            sig_in  => to_X01Z(usb_dn_bus),
            sig_out => usb_dn_i
        );

    usb_phy_rx_inst : entity work.usb_phy_rx
        port map (
            clk                  => clk,
            rst_n                => rst_n,
            usb_dp_i             => usb_dp_i,
            usb_dn_i             => usb_dn_i,
            tx_active            => usb_oe_dev,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid
        );

    usb_packet_deserializer_inst : entity work.usb_packet_deserializer
        port map (
            clk                  => clk,
            rst_n                => rst_n,
            usb_line_state       => usb_line_state,
            usb_line_state_valid => usb_line_state_valid,
            packet_tdata         => rx_packet_tdata,
            packet_tkeep         => rx_packet_tkeep,
            packet_tdest         => rx_packet_tdest,
            packet_tlast         => rx_packet_tlast,
            packet_tvalid        => rx_packet_tvalid,
            packet_tready        => rx_packet_tready,
            stuff_error          => open,
            sync_error           => open,
            reset_detected       => open
        );

    usb_packet_parser_inst : entity work.usb_packet_parser
        port map (
            clk            => clk,
            rst_n          => rst_n,
            packet_tdata   => rx_packet_tdata,
            packet_tkeep   => rx_packet_tkeep,
            packet_tdest   => rx_packet_tdest,
            packet_tlast   => rx_packet_tlast,
            packet_tvalid  => rx_packet_tvalid,
            packet_tready  => rx_packet_tready,
            payload_tdata  => open,
            payload_tkeep  => open,
            payload_tdest  => open,
            payload_tlast  => open,
            payload_tvalid => open,
            payload_tready => '1',
            packet         => rx_packet
        );

    -- Stand-in for the usb_protocol decision: one registered cycle from packet to response.
    -- The IN payload arrives payload_latency cycles after the response starts, like a RAM read.
    responder : process (clk, rst_n)
    begin
        if (rst_n = '0') then
            tx_packet <= PACKET_INIT;
            payload_remaining <= (others => '0');
            payload_delay <= (others => '0');
        elsif rising_edge(clk) then
            tx_packet.valid <= '0';
            if (payload_delay /= 0) then
                payload_delay <= payload_delay - 1;
            end if;
            if (payload_fire = '1') then
                payload_remaining <= payload_remaining - 1;
            end if;
            if (rx_packet.valid = '1' and rx_packet.pid_ok = '1' and rx_packet.crc_ok = '1') then
                case rx_packet.pid is
                    when PID_DATA0 | PID_DATA1 =>
                        tx_packet <= PACKET_INIT;
                        tx_packet.pid <= PID_ACK;
                        tx_packet.packet_type <= HANDSHAKE;
                        tx_packet.pid_ok <= '1';
                        tx_packet.valid <= '1';
                    when PID_IN =>
                        tx_packet <= PACKET_INIT;
                        tx_packet.pid <= PID_DATA1;
                        tx_packet.packet_type <= DATA;
                        tx_packet.pid_ok <= '1';
                        tx_packet.payload_length <= in_length;
                        tx_packet.valid <= '1';
                        payload_remaining <= unsigned(in_length) when (unsigned(in_length) /= 0) else to_unsigned(1, 10);
                        payload_delay <= unsigned(payload_latency);
                    when others =>
                        null;
                end case;
            end if;
        end if;
    end process;

    tx_payload_tvalid <= '1' when (payload_remaining /= 0 and payload_delay = 0) else '0';
    tx_payload_tlast <= '1' when (payload_remaining = 1) else '0';
    tx_payload_tkeep <= "0" when (unsigned(in_length) = 0) else "1";
    tx_payload_tdata <= std_ulogic_vector(resize(unsigned(in_length) - payload_remaining, 8));
    payload_fire <= tx_payload_tvalid and tx_payload_tready;

    usb_packet_generator_inst : entity work.usb_packet_generator
        port map (
            clk            => clk,
            rst_n          => rst_n,
            packet         => tx_packet,
            payload_tdata  => tx_payload_tdata,
            payload_tkeep  => tx_payload_tkeep,
            payload_tdest  => (others => '0'),
            payload_tlast  => tx_payload_tlast,
            payload_tvalid => tx_payload_tvalid,
            payload_tready => tx_payload_tready,
            packet_tdata   => tx_packet_tdata,
            packet_tkeep   => tx_packet_tkeep,
            packet_tdest   => tx_packet_tdest,
            packet_tlast   => tx_packet_tlast,
            packet_tvalid  => tx_packet_tvalid,
            packet_tready  => tx_packet_tready
        );

    usb_packet_serializer_inst : entity work.usb_packet_serializer
        port map (
            clk           => clk,
            rst_n         => rst_n,
            packet_tdata  => tx_packet_tdata,
            packet_tkeep  => tx_packet_tkeep,
            packet_tdest  => tx_packet_tdest,
            packet_tlast  => tx_packet_tlast,
            packet_tvalid => tx_packet_tvalid,
            packet_tready => tx_packet_tready,
            stream_tdata  => serial_tdata,
            stream_tkeep  => serial_tkeep,
            stream_tdest  => serial_tdest,
            stream_tlast  => serial_tlast,
            stream_tvalid => serial_tvalid,
            stream_tready => serial_tready
        );

    usb_phy_tx_inst : entity work.usb_phy_tx
        port map (
            clk           => clk,
            rst_n         => rst_n,
            serial_tdata  => serial_tdata,
            serial_tkeep  => serial_tkeep,
            serial_tdest  => serial_tdest,
            serial_tlast  => serial_tlast,
            serial_tvalid => serial_tvalid,
            serial_tready => serial_tready,
            usb_oe        => usb_oe_dev,
            usb_dp        => usb_dp_dev,
            usb_dn        => usb_dn_dev
        );

end architecture;
