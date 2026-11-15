library ieee;
use ieee.std_logic_1164.all;

entity cdc_handshake is
    generic (
        DATA_WIDTH : natural := 8;
        CDC_STAGES : natural := 2
    );
    port (
        clk_src   : in  std_ulogic;
        rst_src_n : in  std_ulogic;
        src_data  : in  std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        src_valid : in  std_ulogic;
        src_ready : out std_ulogic;

        clk_dst   : in  std_ulogic;
        rst_dst_n : in  std_ulogic;
        dst_data  : out std_ulogic_vector(DATA_WIDTH - 1 downto 0);
        dst_valid : out std_ulogic
    );
end entity;

architecture rtl of cdc_handshake is

    signal busy : std_ulogic;
    signal src_fire : std_ulogic;
    signal held_data : std_ulogic_vector(DATA_WIDTH - 1 downto 0);
    signal request : std_ulogic;
    signal acknowledge : std_ulogic;

begin

    src_ready <= not busy;
    src_fire <= src_valid and not busy;

    -- held_data is read in the destination domain; it stays stable while busy.
    process (clk_src, rst_src_n)
    begin
        if (rst_src_n = '0') then
            busy <= '0';
            held_data <= (others => '0');
        elsif rising_edge(clk_src) then
            if (src_fire = '1') then
                busy <= '1';
                held_data <= src_data;
            elsif (acknowledge = '1') then
                busy <= '0';
            end if;
        end if;
    end process;

    request_inst : entity work.cdc_pulse
        generic map (
            CDC_STAGES => CDC_STAGES
        )
        port map (
            clk_src   => clk_src,
            rst_src_n => rst_src_n,
            src_pulse => src_fire,
            clk_dst   => clk_dst,
            rst_dst_n => rst_dst_n,
            dst_pulse => request
        );

    process (clk_dst, rst_dst_n)
    begin
        if (rst_dst_n = '0') then
            dst_valid <= '0';
            dst_data <= (others => '0');
        elsif rising_edge(clk_dst) then
            dst_valid <= request;
            if (request = '1') then
                dst_data <= held_data;
            end if;
        end if;
    end process;

    acknowledge_inst : entity work.cdc_pulse
        generic map (
            CDC_STAGES => CDC_STAGES
        )
        port map (
            clk_src   => clk_dst,
            rst_src_n => rst_dst_n,
            src_pulse => request,
            clk_dst   => clk_src,
            rst_dst_n => rst_src_n,
            dst_pulse => acknowledge
        );

end architecture;
