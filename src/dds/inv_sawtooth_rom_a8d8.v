//=============================================================================
// 反锯齿波ROM查找表 (Inverse Sawtooth Wave / Ramp Down)
// 8位地址，8位数据
// 波形：从255到0线性递减
//=============================================================================
module inv_sawtooth_rom_a8d8(
        addr,
        clk,
        q
    );

    parameter DATA_WIDTH = 8;
    parameter ADDR_WIDTH = 8;

    input clk;
    input [(ADDR_WIDTH-1):0] addr;
    output reg [(DATA_WIDTH-1):0] q;

    // Declare the ROM variable
    reg [DATA_WIDTH-1:0] rom[2**ADDR_WIDTH-1:0];

    // 生成反锯齿波：线性递减 255 -> 0
    integer i;
    initial begin
        for (i = 0; i < 256; i = i + 1) begin
            rom[i] = 255 - i[7:0];  // 递减波形
        end
    end

    always @ (posedge clk) begin
        q <= rom[addr];
    end

endmodule
