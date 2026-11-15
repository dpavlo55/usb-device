library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use ieee.math_real.all;

use work.usb_pkg.all;

entity usb_packet_deserializer is
    port (
        clk                  : in  std_ulogic;
        rst_n                : in  std_ulogic;

        usb_line_state       : in  usb_line_state_t;
        usb_line_state_valid : in  std_ulogic;

        packet_tdata         : out std_ulogic_vector(7 downto 0);
        packet_tkeep         : out std_ulogic_vector(0 downto 0);
        packet_tdest         : out std_ulogic_vector(3 downto 0);
        packet_tlast         : out std_ulogic;
        packet_tvalid        : out std_ulogic;
        packet_tready        : in  std_ulogic;

        stuff_error          : out std_ulogic;
        sync_error           : out std_ulogic;
        reset_detected       : out std_ulogic
    );
end entity usb_packet_deserializer;

architecture rtl of usb_packet_deserializer is

    constant MAX_CONSECUTIVE_ONES : natural := 6;
    constant SYNC_PATTERN : std_ulogic_vector(7 downto 0) := "10000000";
    constant MIN_RESET_DURATION_CLOCKS : natural := 120 - 1;
    constant LAST_BIT : natural := 7;

    type state_t is (
        IDLE,
        SYNC,
        DATA,
        ERR
    );

    signal usb_line_state_d : usb_line_state_t;
    signal usb_line_state_d_valid : std_ulogic;
    signal usb_line_state_dd : usb_line_state_t;
    signal usb_line_state_dd_valid : std_ulogic;

    signal data_bit : std_ulogic;
    signal data_bit_valid : std_ulogic;

    signal usb_line_state_is_jk : std_ulogic;

    signal unstuff_en : std_ulogic;
    signal data_unstuffed : std_ulogic;
    signal data_unstuffed_valid : std_ulogic;
    signal consecutive_ones : unsigned(2 downto 0);

    signal shift_reg : std_ulogic_vector(7 downto 0);
    signal shift_reg_en : std_ulogic;
    signal shift_cnt : unsigned(2 downto 0);

    signal sop_detected : std_ulogic;
    signal eop_detected : std_ulogic;
    signal rx_active : std_ulogic;
    signal se0_duration_cnt : natural range 0 to MIN_RESET_DURATION_CLOCKS;

    signal state : state_t;
    signal output_en : std_ulogic;

    signal byte : std_ulogic_vector(7 downto 0);
    signal byte_valid : std_ulogic;

    signal cdata : std_ulogic_vector(7 downto 0);
    signal ckeep : std_ulogic_vector(0 downto 0);
    signal cdest : std_ulogic_vector(3 downto 0);
    signal clast : std_ulogic;
    signal cvalid : std_ulogic;
    signal cready : std_ulogic;

    signal eop_pending : std_ulogic;
    signal stage_can_load : std_ulogic;
    signal byte_event : std_ulogic;
    signal eop_event : std_ulogic;

begin

    process (clk)
    begin
        if rising_edge(clk) then
            usb_line_state_d_valid <= '0';
            usb_line_state_dd_valid <= '0';
            if (usb_line_state_valid = '1') then
                usb_line_state_d <= usb_line_state;
                usb_line_state_d_valid <= '1';
            end if;
            if (usb_line_state_d_valid = '1') then
                usb_line_state_dd <= usb_line_state_d;
                usb_line_state_dd_valid <= '1';
            end if;
        end if;
    end process;

    ----------------------------------------------------------------------------
    -- NRZI decoder
    ----------------------------------------------------------------------------

    usb_line_state_is_jk <= '1' when (
        is_usb_line_state_jk(usb_line_state) and
        is_usb_line_state_jk(usb_line_state_d)
    ) else '0';

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            data_bit <= '0';
            data_bit_valid <= '0';
        elsif rising_edge(clk) then
            data_bit_valid <= '0';
            if (sop_detected = '1' or rx_active = '1') then
                if (usb_line_state_is_jk = '1' and usb_line_state_valid = '1') then
                    if (usb_line_state = usb_line_state_d) then
                        data_bit <= '1';
                    else
                        data_bit <= '0';
                    end if;
                    data_bit_valid <= '1';
                end if;
            end if;
        end if;
    end process;

    ----------------------------------------------------------------------------
    -- RESET, SOP, and EOP detection
    ----------------------------------------------------------------------------

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            reset_detected <= '0';
            se0_duration_cnt <= 0;
        elsif rising_edge(clk) then
            if (usb_line_state = USB_FS_SE0) then
                if (se0_duration_cnt >= MIN_RESET_DURATION_CLOCKS) then
                    reset_detected <= '1';
                else
                    se0_duration_cnt <= se0_duration_cnt + 1;
                end if;
            else
                se0_duration_cnt <= 0;
                reset_detected <= '0';
            end if;
        end if;
    end process;

    sop_detected <= '1' when (
        (rx_active = '0') and
        (usb_line_state_valid = '1') and
        (usb_line_state = USB_FS_K) and
        (usb_line_state_d = USB_FS_J)
    ) else '0';

    eop_detected <= '1' when (
        (reset_detected = '0') and
        (rx_active = '1') and
        (usb_line_state_valid = '1') and
        (usb_line_state = USB_FS_J) and
        (usb_line_state_d = USB_FS_SE0) and
        (usb_line_state_dd = USB_FS_SE0)) else '0';

    ----------------------------------------------------------------------------
    -- control FSM
    ----------------------------------------------------------------------------

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            state <= IDLE;
            rx_active <= '0';
            unstuff_en <= '0';
            shift_reg_en <= '0';
            output_en <= '0';
            sync_error <= '0';
        elsif rising_edge(clk) then
            if (reset_detected = '1') then
                state <= IDLE;
                rx_active <= '0';
                unstuff_en <= '0';
                shift_reg_en <= '0';
                output_en <= '0';
                sync_error <= '0';
            else
                case state is
                    ------------------------------------------------------------
                    when IDLE =>
                        if (sop_detected = '1') then
                            state <= SYNC;
                            rx_active <= '1';
                            unstuff_en <= '1';
                            shift_reg_en <= '1';
                        end if;
                    ------------------------------------------------------------
                    when SYNC =>
                        if (byte_valid = '1') then
                            if (byte = SYNC_PATTERN) then
                                state <= DATA;
                                output_en <= '1';
                            else
                                state <= ERR;
                                sync_error <= '1';
                                unstuff_en <= '0';
                                shift_reg_en <= '0';
                                output_en <= '0';
                            end if;
                        end if;
                    ------------------------------------------------------------
                    when DATA =>
                        if (eop_detected = '1') then
                            state <= IDLE;
                            rx_active <= '0';
                            unstuff_en <= '0';
                            shift_reg_en <= '0';
                            output_en <= '0';
                        end if;
                    ------------------------------------------------------------
                    when ERR =>
                        if (eop_detected = '1') then
                            state <= IDLE;
                            rx_active <= '0';
                            sync_error <= '0';
                            unstuff_en <= '0';
                            shift_reg_en <= '0';
                            output_en <= '0';
                        end if;
                end case;
            end if;
        end if;
    end process;

    ----------------------------------------------------------------------------
    -- Bit unstuffing
    ----------------------------------------------------------------------------

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            data_unstuffed <= '0';
            data_unstuffed_valid <= '0';
            stuff_error <= '0';
            consecutive_ones <= (others => '0');
        elsif rising_edge(clk) then
            --default value
            data_unstuffed_valid <= '0';
            if (unstuff_en = '1' and data_bit_valid = '1') then
                -- always output the current data bit
                data_unstuffed <= data_bit;
                if (consecutive_ones = MAX_CONSECUTIVE_ONES) then
                    consecutive_ones <= (others => '0');
                    if (data_bit = '1') then
                        stuff_error <= '1';
                    else
                        -- stuffed bit detected, set the valid to '0'
                        data_unstuffed_valid <= '0';
                    end if;
                else
                    data_unstuffed_valid <= '1';
                    if (data_bit = '1') then
                        consecutive_ones <= consecutive_ones + 1;
                    else
                        consecutive_ones <= (others => '0');
                    end if;
                end if;
            elsif (unstuff_en = '0') then
                consecutive_ones <= (others => '0');
                stuff_error <= '0';
            end if;
        end if;
    end process;

    ----------------------------------------------------------------------------
    -- shift register for bits to bytes conversion
    ----------------------------------------------------------------------------

    process (clk, rst_n)
        variable shift_reg_bext : std_ulogic_vector(7 downto 0);
    begin
        if (rst_n = '0') then
            shift_reg <= (others => '0');
            shift_cnt <= (others => '0');
            byte <= (others => '0');
            byte_valid <= '0';
        elsif rising_edge(clk) then
            byte_valid <= '0';
            if (shift_reg_en = '1' and data_unstuffed_valid = '1') then
                shift_reg_bext := data_unstuffed & shift_reg(7 downto 1);
                shift_reg <= shift_reg_bext;
                shift_cnt <= shift_cnt + 1;
                if (shift_cnt = LAST_BIT) then
                    shift_cnt <= (others => '0');
                    byte <= shift_reg_bext;
                    byte_valid <= '1';
                end if;
            elsif (shift_reg_en = '0') then
                shift_reg <= (others => '0');
                shift_cnt <= (others => '0');
            end if;
        end if;
    end process;

    ----------------------------------------------------------------------------
    -- AXI Stream interface
    ----------------------------------------------------------------------------

    stage_can_load <= '1' when (cvalid = '0' or cready = '1') else '0';
    byte_event <= '1' when (output_en = '1' and byte_valid = '1') else '0';
    eop_event <= '1' when (output_en = '1' and eop_detected = '1') else '0';

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            cdata <= (others => '0');
            ckeep <= (others => '0');
            cdest <= (others => '0');
            clast <= '0';
            cvalid <= '0';
            eop_pending <= '0';
        elsif rising_edge(clk) then
            if (eop_event = '1') then
                if (stage_can_load = '1' and byte_event = '0') then
                    eop_pending <= '0';
                else
                    eop_pending <= '1';
                end if;
            elsif (eop_pending = '1' and
                    stage_can_load = '1' and byte_event = '0') then
                eop_pending <= '0';
            end if;

            if (stage_can_load = '1') then
                cvalid <= '0';
                clast <= '0';
                if (byte_event = '1') then
                    cdata <= byte;
                    ckeep <= (others => '1');
                    cdest <= (others => '0');
                    cvalid <= '1';
                elsif (eop_pending = '1' or eop_event = '1') then
                    cdata <= (others => '0');
                    ckeep <= "0";
                    cdest <= (others => '0');
                    clast <= '1';
                    cvalid <= '1';
                end if;
            end if;
        end if;
    end process;

    output_buffer_inst : entity work.output_buffer
        port map (
            clk     => clk,
            rst_n   => rst_n,
            cdata   => cdata,
            ckeep   => ckeep,
            cdest   => cdest,
            clast   => clast,
            cvalid  => cvalid,
            cready  => cready,
            odata   => packet_tdata,
            okeep   => packet_tkeep,
            odest   => packet_tdest,
            olast   => packet_tlast,
            ovalid  => packet_tvalid,
            oready  => packet_tready
        );

end architecture;
