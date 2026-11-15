library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_ram_usb_adapter is
    generic (
        ADDR_WIDTH : natural := 10;
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        endpoint_base_address : in vector_array_t(0 to 2 ** DEST_WIDTH - 1)(ADDR_WIDTH - 1 downto 0);
        write_allowed         : in std_ulogic_vector(2 ** DEST_WIDTH - 1 downto 0);
        read_command          : in ram_read_command_t(base_address(ADDR_WIDTH - 1 downto 0));

        out_report : out packet_report_t;
        in_report  : out packet_report_t;

        rx_payload_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        rx_payload_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        rx_payload_tdest  : in  std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        rx_payload_tlast  : in  std_ulogic;
        rx_payload_tvalid : in  std_ulogic;
        rx_payload_tready : out std_ulogic;

        tx_payload_tdata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        tx_payload_tkeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        tx_payload_tdest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        tx_payload_tlast  : out std_ulogic;
        tx_payload_tvalid : out std_ulogic;
        tx_payload_tready : in  std_ulogic;

        ram_en    : out std_ulogic;
        ram_we    : out std_ulogic;
        ram_addr  : out std_ulogic_vector(ADDR_WIDTH - 1 downto 0);
        ram_wdata : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        ram_rdata : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0)
    );
end entity usb_ram_usb_adapter;

architecture rtl of usb_ram_usb_adapter is

    subtype endpoint_t is natural range 0 to 2 ** DEST_WIDTH - 1;

    signal rx : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));
    signal tx : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal rx_fire : std_ulogic;
    signal rx_first : std_ulogic;
    signal rx_addr : unsigned(ADDR_WIDTH - 1 downto 0);
    signal rx_endpoint : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal rx_allowed : std_ulogic;
    signal rx_count : unsigned(out_report.byte_count'range);
    signal packet_endpoint : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal packet_allowed : std_ulogic;
    signal packet_count : unsigned(out_report.byte_count'range);
    signal next_count : unsigned(out_report.byte_count'range);
    signal write_endpoint : endpoint_t;
    signal write_addr : unsigned(ADDR_WIDTH - 1 downto 0);
    signal write_fire : std_ulogic;

    signal reading : std_ulogic;
    signal read_endpoint : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal read_addr : unsigned(ADDR_WIDTH - 1 downto 0);
    signal read_length : std_ulogic_vector(read_command.length'range);
    signal read_remaining : unsigned(read_command.length'range);
    signal read_empty : std_ulogic;
    signal read_last : std_ulogic;
    signal read_fire : std_ulogic;

    signal stage_ready : std_ulogic;
    signal stage_valid : std_ulogic;
    signal stage_keep : std_ulogic;
    signal stage_dest : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal stage_last : std_ulogic;

begin

    input_buffer_inst : entity work.input_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => rx_payload_tdata,
            ikeep  => rx_payload_tkeep,
            idest  => rx_payload_tdest,
            ilast  => rx_payload_tlast,
            ivalid => rx_payload_tvalid,
            iready => rx_payload_tready,
            cdata  => rx.data,
            ckeep  => rx.keep,
            cdest  => rx.dest,
            clast  => rx.last,
            cvalid => rx.valid,
            cready => rx.ready
        );

    output_buffer_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => tx.data,
            ckeep  => tx.keep,
            cdest  => tx.dest,
            clast  => tx.last,
            cvalid => tx.valid,
            cready => tx.ready,
            odata  => tx_payload_tdata,
            okeep  => tx_payload_tkeep,
            odest  => tx_payload_tdest,
            olast  => tx_payload_tlast,
            ovalid => tx_payload_tvalid,
            oready => tx_payload_tready
        );

    -- Any RAM access reloads the RAM output register, so it may only happen
    -- when the byte held there is gone or is being taken this cycle.
    stage_ready <= (not stage_valid) or tx.ready;

    -- OUT has priority: a beat is consumed whenever the RAM can be accessed,
    -- and a read is only issued when no OUT beat is waiting.
    rx.ready <= stage_ready;
    rx_fire <= rx.valid and rx.ready;
    read_fire <= reading and stage_ready and not rx.valid;

    -- The endpoint, its base address and its write permission are taken from
    -- the first beat and hold for the whole packet.
    write_endpoint <= to_integer(unsigned(rx.dest));
    packet_endpoint <= rx.dest when (rx_first = '1') else rx_endpoint;
    packet_allowed <= write_allowed(write_endpoint) when (rx_first = '1') else rx_allowed;
    packet_count <= (others => '0') when (rx_first = '1') else rx_count;
    next_count <= packet_count + 1 when (rx.keep(0) = '1') else packet_count;
    write_addr <=
        unsigned(endpoint_base_address(write_endpoint)) when (rx_first = '1') else
        rx_addr;
    write_fire <= rx_fire and rx.keep(0) and packet_allowed;

    ram_en <= write_fire or (read_fire and not read_empty);
    ram_we <= write_fire;
    ram_addr <=
        std_ulogic_vector(write_addr) when (rx.valid = '1') else
        std_ulogic_vector(read_addr);
    ram_wdata <= rx.data;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            rx_first <= '1';
            rx_addr <= (others => '0');
            rx_endpoint <= (others => '0');
            rx_allowed <= '0';
            rx_count <= (others => '0');
        elsif rising_edge(clk) then
            if (rx_fire = '1') then
                rx_addr <= write_addr + 1;
                rx_first <= rx.last;
                rx_endpoint <= packet_endpoint;
                rx_allowed <= packet_allowed;
                rx_count <= next_count;
            end if;
        end if;
    end process;

    out_report.endpoint_number <= std_ulogic_vector(resize(
        unsigned(packet_endpoint), out_report.endpoint_number'length));
    out_report.byte_count <= std_ulogic_vector(next_count);
    out_report.accepted <= packet_allowed;
    out_report.valid <= rx_fire and rx.last;

    read_empty <= '1' when (read_remaining = 0) else '0';
    read_last <= '1' when (read_remaining <= 1) else '0';

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            reading <= '0';
            read_endpoint <= (others => '0');
            read_addr <= (others => '0');
            read_length <= (others => '0');
            read_remaining <= (others => '0');
        elsif rising_edge(clk) then
            if (reading = '0') then
                if (read_command.start = '1') then
                    reading <= '1';
                    read_endpoint <=
                        read_command.endpoint_number(DEST_WIDTH - 1 downto 0);
                    read_addr <= unsigned(read_command.base_address);
                    read_length <= read_command.length;
                    read_remaining <= unsigned(read_command.length);
                end if;
            elsif (read_fire = '1') then
                read_addr <= read_addr + 1;
                read_remaining <= read_remaining - 1;
                if (read_last = '1') then
                    reading <= '0';
                end if;
            end if;
        end if;
    end process;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            stage_valid <= '0';
            stage_keep <= '0';
            stage_dest <= (others => '0');
            stage_last <= '0';
        elsif rising_edge(clk) then
            if (stage_ready = '1') then
                stage_valid <= read_fire;
                stage_keep <= not read_empty;
                stage_dest <= read_endpoint;
                stage_last <= read_last;
            end if;
        end if;
    end process;

    tx.data <= ram_rdata;
    tx.keep <= (others => stage_keep);
    tx.dest <= stage_dest;
    tx.last <= stage_last;
    tx.valid <= stage_valid;

    in_report.endpoint_number <= std_ulogic_vector(resize(
        unsigned(tx_payload_tdest), in_report.endpoint_number'length));
    in_report.byte_count <= read_length;
    in_report.accepted <= '1';
    in_report.valid <=
        tx_payload_tvalid and tx_payload_tready and tx_payload_tlast;

end architecture rtl;
