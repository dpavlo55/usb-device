library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package stream_pkg is

    type stream_t is record
        data  : std_ulogic_vector;
        keep  : std_ulogic_vector;
        dest  : std_ulogic_vector;
        last  : std_ulogic;
        valid : std_ulogic;
        ready : std_ulogic;
    end record;

    type stream_array_t is array (natural range <>) of stream_t;

end package;

package body stream_pkg is

end package body;