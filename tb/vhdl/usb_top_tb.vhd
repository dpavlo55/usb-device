library ieee;
use ieee.std_logic_1164.all;

library neorv32;
use work.usb_pkg.all;
use work.usb_device_regs_pkg.USB_DEVICE_REGS_MIN_ADDR_WIDTH;
use neorv32.neorv32_package.all;

entity usb_top_tb is
    port (
        clk_sys     : in  std_ulogic;
        rst_sys_n   : in  std_ulogic;
        clk_usb     : in  std_ulogic;
        rst_usb_n   : in  std_ulogic;
        usb_vbus    : in  std_ulogic;
        usb_pu      : out std_ulogic;
        usb_irq     : out std_ulogic;
        usb_dp_host : in  std_logic;
        usb_dn_host : in  std_logic;
        usb_dp      : out std_logic;
        usb_dn      : out std_logic;
        sw_test_status : out std_ulogic_vector(31 downto 0)
    );
end entity;

architecture sim of usb_top_tb is

    signal usb_dp_bus : std_logic;
    signal usb_dn_bus : std_logic;

    signal xbus_req : xbus_req_t;
    signal xbus_rsp : xbus_rsp_t;
    constant USB_DEVICE_BASE : std_ulogic_vector(31 downto 0) := x"F0000000";
    signal usb_selected : boolean;
    signal sw_test_selected : boolean;
    signal usb_xbus_req : xbus_req_t;
    signal usb_xbus_rsp : xbus_rsp_t;
    signal cpu_xbus_addr : std_ulogic_vector(31 downto 0);
    signal cpu_xbus_data : std_ulogic_vector(31 downto 0);
    signal cpu_xbus_cti  : std_ulogic_vector(2 downto 0);
    signal cpu_xbus_tag  : std_ulogic_vector(2 downto 0);
    signal cpu_xbus_we   : std_ulogic;
    signal cpu_xbus_sel  : std_ulogic_vector(3 downto 0);
    signal cpu_xbus_stb  : std_ulogic;
    signal cpu_xbus_cyc  : std_ulogic;

    signal axi_awaddr  : std_logic_vector(31 downto 0);
    signal axi_awlen   : std_logic_vector(7 downto 0);
    signal axi_awsize  : std_logic_vector(2 downto 0);
    signal axi_awburst : std_logic_vector(1 downto 0);
    signal axi_awcache : std_logic_vector(3 downto 0);
    signal axi_awprot  : std_logic_vector(2 downto 0);
    signal axi_awvalid : std_logic;
    signal axi_awready : std_logic;
    signal axi_wdata   : std_logic_vector(31 downto 0);
    signal axi_wstrb   : std_logic_vector(3 downto 0);
    signal axi_wlast   : std_logic;
    signal axi_wvalid  : std_logic;
    signal axi_wready  : std_logic;
    signal axi_araddr  : std_logic_vector(31 downto 0);
    signal axi_arlen   : std_logic_vector(7 downto 0);
    signal axi_arsize  : std_logic_vector(2 downto 0);
    signal axi_arburst : std_logic_vector(1 downto 0);
    signal axi_arcache : std_logic_vector(3 downto 0);
    signal axi_arprot  : std_logic_vector(2 downto 0);
    signal axi_arvalid : std_logic;
    signal axi_arready : std_logic;
    signal axi_rdata   : std_logic_vector(31 downto 0);
    signal axi_rresp   : std_logic_vector(1 downto 0);
    signal axi_rlast   : std_logic;
    signal axi_rvalid  : std_logic;
    signal axi_rready  : std_logic;
    signal axi_bresp   : std_logic_vector(1 downto 0);
    signal axi_bvalid  : std_logic;
    signal axi_bready  : std_logic;

begin

    line_state_decoder_checks : process
    begin
        assert to_usb_line_state('1', '0') = USB_FS_J
            report "D+/D- 10 must decode as USB_FS_J"
            severity failure;
        assert to_usb_line_state('0', '1') = USB_FS_K
            report "D+/D- 01 must decode as USB_FS_K"
            severity failure;
        assert to_usb_line_state('0', '0') = USB_FS_SE0
            report "D+/D- 00 must decode as USB_FS_SE0"
            severity failure;
        assert to_usb_line_state('1', '1') = USB_FS_SE1
            report "D+/D- 11 must decode as USB_FS_SE1"
            severity failure;
        assert to_usb_line_state('Z', '0') = USB_LINE_INVALID
            report "High impedance input must decode as USB_LINE_INVALID"
            severity failure;
        assert to_usb_line_state('X', '1') = USB_LINE_INVALID
            report "Unknown input must decode as USB_LINE_INVALID"
            severity failure;
        assert to_usb_line_state('H', '0') = USB_LINE_INVALID
            report "Weak logic input must decode as USB_LINE_INVALID"
            severity failure;
        wait;
    end process;

    -- Resolve the external host drive, the DUT drive, and the attached
    -- Full-Speed device's idle bias on an internal bidirectional bus.
    usb_dp_bus <= usb_dp_host;
    usb_dn_bus <= usb_dn_host;
    usb_dp_bus <= 'H';
    usb_dn_bus <= 'L';

    cpu_inst : entity neorv32.neorv32_top
        generic map (
            CLOCK_FREQUENCY => 60000000,
            BOOT_MODE_SELECT => 2,
            CPU_RF_ARCH_SEL => 0,
            CPU_FAST_SHIFT_EN => true,
            RISCV_ISA_M => true,
            RISCV_ISA_Zicntr => true,
            IMEM_EN => true,
            IMEM_SIZE => 32*1024,
            DMEM_EN => true,
            DMEM_SIZE => 16*1024,
            ICACHE_EN => false,
            DCACHE_EN => false,
            XBUS_EN => true,
            IO_UART0_EN => true
        )
        port map (
            clk_i => clk_sys,
            rstn_i => rst_sys_n,
            rstn_ocd_o => open,
            rstn_wdt_o => open,
            xbus_adr_o => cpu_xbus_addr,
            xbus_dat_o => cpu_xbus_data,
            xbus_cti_o => cpu_xbus_cti,
            xbus_tag_o => cpu_xbus_tag,
            xbus_we_o => cpu_xbus_we,
            xbus_sel_o => cpu_xbus_sel,
            xbus_stb_o => cpu_xbus_stb,
            xbus_cyc_o => cpu_xbus_cyc,
            xbus_dat_i => xbus_rsp.data,
            xbus_ack_i => xbus_rsp.ack,
            xbus_err_i => xbus_rsp.err,
            uart0_txd_o => open,
            uart0_rxd_i => '1',
            irq_mei_i => usb_irq
        );

    xbus_req <= (
        addr => cpu_xbus_addr,
        data => cpu_xbus_data,
        cti => cpu_xbus_cti,
        tag => cpu_xbus_tag,
        we => cpu_xbus_we,
        sel => cpu_xbus_sel,
        stb => cpu_xbus_stb,
        cyc => cpu_xbus_cyc
    );

    -- Decode exactly the register/RAM window. Unmapped accesses fail
    -- immediately instead of aliasing the USB registers or waiting for timeout.
    usb_selected <= xbus_req.addr(31 downto USB_DEVICE_REGS_MIN_ADDR_WIDTH) =
                    USB_DEVICE_BASE(31 downto USB_DEVICE_REGS_MIN_ADDR_WIDTH);
    sw_test_selected <= xbus_req.addr = x"F0100000";
    usb_xbus_req <= xbus_req when usb_selected else xbus_req_terminate_c;
    -- Hold the test-register acknowledgement for the whole bus cycle so the
    -- CPU sees it after its pending transaction has been registered.
    xbus_rsp <= usb_xbus_rsp when usb_selected else
                (data => (others => '0'), ack => xbus_req.cyc,
                 err => '0') when sw_test_selected else
                (data => (others => '0'), ack => '0',
                 err => xbus_req.cyc and xbus_req.stb);

    process(clk_sys)
    begin
        if rising_edge(clk_sys) then
            if rst_sys_n = '0' then
                sw_test_status <= (others => '0');
            elsif sw_test_selected and xbus_req.cyc = '1' and xbus_req.stb = '1'
                  and xbus_req.we = '1' then
                sw_test_status <= xbus_req.data;
            end if;
        end if;
    end process;

    xbus_axi_bridge_inst : entity neorv32.xbus2axi4_bridge
        generic map (
            BURST_EN => false,
            BURST_LEN => 4
        )
        port map (
            clk => clk_sys,
            resetn => rst_sys_n,
            xbus_req_i => usb_xbus_req,
            xbus_rsp_o => usb_xbus_rsp,
            m_axi_awaddr => axi_awaddr,
            m_axi_awlen => axi_awlen,
            m_axi_awsize => axi_awsize,
            m_axi_awburst => axi_awburst,
            m_axi_awcache => axi_awcache,
            m_axi_awprot => axi_awprot,
            m_axi_awvalid => axi_awvalid,
            m_axi_awready => axi_awready,
            m_axi_wdata => axi_wdata,
            m_axi_wstrb => axi_wstrb,
            m_axi_wlast => axi_wlast,
            m_axi_wvalid => axi_wvalid,
            m_axi_wready => axi_wready,
            m_axi_araddr => axi_araddr,
            m_axi_arlen => axi_arlen,
            m_axi_arsize => axi_arsize,
            m_axi_arburst => axi_arburst,
            m_axi_arcache => axi_arcache,
            m_axi_arprot => axi_arprot,
            m_axi_arvalid => axi_arvalid,
            m_axi_arready => axi_arready,
            m_axi_rdata => axi_rdata,
            m_axi_rresp => axi_rresp,
            m_axi_rlast => axi_rlast,
            m_axi_rvalid => axi_rvalid,
            m_axi_rready => axi_rready,
            m_axi_bresp => axi_bresp,
            m_axi_bvalid => axi_bvalid,
            m_axi_bready => axi_bready
        );

    -- The bridge emits only single-beat transfers; AXI4-Lite has no length,
    -- burst, cache, last, or response-last channels.
    axi_rlast <= '1';

    usb_top_inst : entity work.usb_top
    port map (
        clk_sys   => clk_sys,
        rst_sys_n => rst_sys_n,
        clk_usb   => clk_usb,
        rst_usb_n => rst_usb_n,
        axil_awaddr => axi_awaddr(USB_DEVICE_REGS_MIN_ADDR_WIDTH - 1 downto 0),
        axil_awprot => axi_awprot,
        axil_awvalid => axi_awvalid,
        axil_awready => axi_awready,
        axil_wdata => axi_wdata,
        axil_wstrb => axi_wstrb,
        axil_wvalid => axi_wvalid,
        axil_wready => axi_wready,
        axil_bresp => axi_bresp,
        axil_bvalid => axi_bvalid,
        axil_bready => axi_bready,
        axil_araddr => axi_araddr(USB_DEVICE_REGS_MIN_ADDR_WIDTH - 1 downto 0),
        axil_arprot => axi_arprot,
        axil_arvalid => axi_arvalid,
        axil_arready => axi_arready,
        axil_rdata => axi_rdata,
        axil_rresp => axi_rresp,
        axil_rvalid => axi_rvalid,
        axil_rready => axi_rready,
        usb_vbus  => usb_vbus,
        usb_pu    => usb_pu,
        irq       => usb_irq,
        usb_dp    => usb_dp_bus,
        usb_dn    => usb_dn_bus
    );

    usb_dp <= usb_dp_bus;
    usb_dn <= usb_dn_bus;

end architecture;
