library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.usb_pkg.all;
use work.stream_pkg.all;

entity usb_setup_stage is
    generic (
        DATA_WIDTH : natural := 8;
        KEEP_WIDTH : natural := (DATA_WIDTH + 7) / 8;
        DEST_WIDTH : natural := 4
    );
    port (
        clk   : in std_ulogic;
        rst_n : in std_ulogic;

        routed_tdata  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        routed_tkeep  : in  std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        routed_tdest  : in  std_ulogic_vector(DEST_WIDTH downto 0);
        routed_tlast  : in  std_ulogic;
        routed_tvalid : in  std_ulogic;
        routed_tready : out std_ulogic;

        setup_received : in  std_ulogic;
        setup_writing  : out std_ulogic;
        setup_entry    : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        setup_written  : out std_ulogic;

        write_tdata  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        write_tkeep  : out std_ulogic_vector(KEEP_WIDTH - 1 downto 0);
        write_tdest  : out std_ulogic_vector(DEST_WIDTH - 1 downto 0);
        write_tlast  : out std_ulogic;
        write_tvalid : out std_ulogic;
        write_tready : in  std_ulogic
    );
end entity usb_setup_stage;

architecture rtl of usb_setup_stage is

    constant SETUP_BYTES : natural := 8;

    signal routed : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH downto 0));
    signal write : stream_t(
        data(DATA_WIDTH - 1 downto 0),
        keep(KEEP_WIDTH - 1 downto 0),
        dest(DEST_WIDTH - 1 downto 0));

    signal routed_fire : std_ulogic;
    signal routed_setup : std_ulogic;
    signal routed_entry : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal first : std_ulogic;

    signal held_data : vector_array_t(0 to SETUP_BYTES - 1)(DATA_WIDTH - 1 downto 0);
    signal held_count : natural range 0 to SETUP_BYTES;
    signal held_entry : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal accepted : std_ulogic;

    signal replay_start : std_ulogic;
    signal replay_entry : std_ulogic_vector(DEST_WIDTH - 1 downto 0);
    signal replaying : std_ulogic;
    signal replay_index : natural range 0 to SETUP_BYTES - 1;
    signal replay_last : std_ulogic;
    signal write_fire : std_ulogic;
    signal last_in_buffer : std_ulogic;
    signal port_last_fire : std_ulogic;

begin

    input_buffer_inst : entity work.input_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH + 1
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            idata  => routed_tdata,
            ikeep  => routed_tkeep,
            idest  => routed_tdest,
            ilast  => routed_tlast,
            ivalid => routed_tvalid,
            iready => routed_tready,
            cdata  => routed.data,
            ckeep  => routed.keep,
            cdest  => routed.dest,
            clast  => routed.last,
            cvalid => routed.valid,
            cready => routed.ready
        );

    routed_setup <= routed.dest(DEST_WIDTH);
    routed_entry <= routed.dest(DEST_WIDTH - 1 downto 0);

    -- An accepted SETUP is written once its last beat is in (between packets,
    -- before the next one starts) and the previous SETUP write has been reported.
    replay_start <= accepted and first and not replaying and not last_in_buffer;
    replay_last <= '1' when (replay_index = SETUP_BYTES - 1) else '0';

    write.valid <= '1' when (replaying = '1') else routed.valid and not routed_setup and not replay_start;
    write.data <= held_data(replay_index) when (replaying = '1') else routed.data;
    write.keep <= (others => '1') when (replaying = '1') else routed.keep;
    write.dest <= replay_entry when (replaying = '1') else routed_entry;
    write.last <= replay_last when (replaying = '1') else routed.last;
    write_fire <= write.valid and write.ready;

    routed.ready <=
        '0' when (replaying = '1' or replay_start = '1') else
        '1' when (routed_setup = '1') else
        write.ready;
    routed_fire <= routed.valid and routed.ready;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            first <= '1';
            held_data <= (others => (others => '0'));
            held_count <= 0;
            held_entry <= (others => '0');
            accepted <= '0';
            replaying <= '0';
            replay_index <= 0;
            replay_entry <= (others => '0');
        elsif rising_edge(clk) then
            if (routed_fire = '1') then
                first <= routed.last;
            end if;

            if (routed_fire = '1' and routed_setup = '1') then
                if (first = '1') then
                    held_count <= 0;
                    if (routed.keep(0) = '1') then
                        held_data(0) <= routed.data;
                        held_count <= 1;
                    end if;
                elsif (routed.keep(0) = '1' and held_count < SETUP_BYTES) then
                    held_data(held_count) <= routed.data;
                    held_count <= held_count + 1;
                end if;
                if (routed.last = '1') then
                    held_entry <= routed_entry;
                end if;
            end if;

            if (replay_start = '1') then
                replaying <= '1';
                replay_index <= 0;
                replay_entry <= held_entry;
                accepted <= '0';
            elsif (write_fire = '1' and replaying = '1') then
                if (replay_last = '1') then
                    replaying <= '0';
                else
                    replay_index <= replay_index + 1;
                end if;
            end if;

            if (setup_received = '1') then
                accepted <= '1';
            end if;
        end if;
    end process;

    output_buffer_inst : entity work.output_buffer
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KEEP_WIDTH => KEEP_WIDTH,
            DEST_WIDTH => DEST_WIDTH
        )
        port map (
            clk    => clk,
            rst_n  => rst_n,
            cdata  => write.data,
            ckeep  => write.keep,
            cdest  => write.dest,
            clast  => write.last,
            cvalid => write.valid,
            cready => write.ready,
            odata  => write_tdata,
            okeep  => write_tkeep,
            odest  => write_tdest,
            olast  => write_tlast,
            ovalid => write_tvalid,
            oready => write_tready
        );

    -- The SETUP is reported once its last beat has left the output buffer.
    port_last_fire <= write_tvalid and write_tready and write_tlast;
    setup_entry <= replay_entry;

    process (clk, rst_n)
    begin
        if (rst_n = '0') then
            setup_writing <= '0';
            last_in_buffer <= '0';
            setup_written <= '0';
        elsif rising_edge(clk) then
            setup_written <= '0';
            if (write_fire = '1' and replaying = '1' and replay_index = 0) then
                setup_writing <= '1';
            end if;
            if (write_fire = '1' and replaying = '1' and replay_last = '1') then
                last_in_buffer <= '1';
            elsif (port_last_fire = '1' and last_in_buffer = '1') then
                last_in_buffer <= '0';
                setup_writing <= '0';
                setup_written <= '1';
            end if;
        end if;
    end process;

end architecture rtl;
