library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity input_output_buffer_tb is
    port (
        clk    : in  std_ulogic;
        rst_n  : in  std_ulogic;
        idata  : in  std_ulogic_vector(7 downto 0);
        ikeep  : in  std_ulogic_vector(0 downto 0);
        idest  : in  std_ulogic_vector(3 downto 0);
        ilast  : in  std_ulogic;
        ivalid : in  std_ulogic;
        iready : out std_ulogic;
        odata  : out std_ulogic_vector(7 downto 0);
        okeep  : out std_ulogic_vector(0 downto 0);
        odest  : out std_ulogic_vector(3 downto 0);
        olast  : out std_ulogic;
        ovalid : out std_ulogic;
        oready : in  std_ulogic
    );
end entity;

architecture sim of input_output_buffer_tb is
    signal cdata  : std_ulogic_vector(7 downto 0);
    signal ckeep  : std_ulogic_vector(0 downto 0);
    signal cdest  : std_ulogic_vector(3 downto 0);
    signal clast  : std_ulogic;
    signal cvalid : std_ulogic;
    signal cready : std_ulogic;
begin

    -- Instantiate input buffer
    input_buffer_inst : entity work.input_buffer
        port map (
            clk     => clk,
            rst_n   => rst_n,
            idata   => idata,
            ikeep   => ikeep,
            idest   => idest,
            ilast   => ilast,
            ivalid  => ivalid,
            iready  => iready,
            cdata   => cdata,
            ckeep   => ckeep,
            cdest   => cdest,
            clast   => clast,
            cvalid  => cvalid,
            cready  => cready
        );

    -- Instantiate output buffer
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
            odata   => odata,
            okeep   => okeep,
            odest   => odest,
            olast   => olast,
            ovalid  => ovalid,
            oready  => oready
        );

end architecture;